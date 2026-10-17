"""Exact year-only metrics and ranking families for the Spotify Charts image studio.

Inputs are verified daily Top 200 rows. Song peaks/days use this year only,
never Spotify's lifetime peakRank/appearancesOnChart. Albums aggregate charting
tracks; no official album-chart position is claimed.
"""
from collections import defaultdict

from common import (ALBUM_MERGES_PATH, ARTIST_GENDERS_PATH, CSV_ROOT,
                    TAYLOR_ARTIST_ID, TRACKS_META_PATH, read_json)
from stats import Blocked, canonical_ids, rank

KINDS = ("songs", "female", "debuts", "female_debuts", "albums", "female_albums")
TITLES = {"songs": "Top Songs", "female": "Top Female Songs", "debuts": "Biggest Debuts",
          "female_debuts": "Biggest Female Debuts", "albums": "Top Albums", "female_albums": "Top Female Albums"}


def song_metrics(rows, year):
    canon = canonical_ids(rows)
    grouped = defaultdict(list)
    for row in rows:
        grouped[canon.get(row["track_id"], row["track_id"])].append(row)
    result = {}
    for key, source in grouped.items():
        source.sort(key=lambda r: r["date"])
        if len(source) != len({r["date"] for r in source}):
            raise Blocked(f"{key}: mapped IDs both chart on the same date; explicit mapping needs review")
        named = next((r for r in reversed(source) if r["track_name"] and r["artist_ids"]), None)
        if named is None:
            raise Blocked(f"{key}: no exact named/credited row")
        entry_dates = {r["entry_date"] for r in source if r["entry_date"]}
        if len(entry_dates) != 1:
            raise Blocked(f"{key}: ambiguous/missing Spotify chart entryDate: {sorted(entry_dates)}")
        entry_date = next(iter(entry_dates))
        debut = next((r for r in source if r["date"] == entry_date), None) if entry_date[:4] == year else None
        if entry_date[:4] == year and (not debut or str(debut["days_on_chart"]) != "1"):
            raise Blocked(f"{key}: {entry_date} debut lacks the exact day-one chart row")
        result[key] = {"key": key, "name": named["track_name"], "artists": named["artists"],
                       "artist_ids": named["artist_ids"].split(" | "), "image_url": named["image_url"],
                       "entry_date": entry_date, "debut": debut, "rows": source}
    return result


def album_metrics(rows, year):
    meta = read_json(TRACKS_META_PATH, {})
    merges = read_json(ALBUM_MERGES_PATH, {})
    overrides = read_json(CSV_ROOT / "album_credits.json", {})
    grouped, unknown = defaultdict(list), set()
    for row in rows:
        m = meta.get(row["track_id"])
        if not m or not m.get("album_id") or not m.get("album_type"):
            unknown.add(f"{row['track_id']} ({row['track_name']})")
            continue
        if m["album_type"] == "single":
            continue
        grouped[merges.get(m["album_id"], m["album_id"])].append(row)
    if unknown:
        raise Blocked(f"Albums: unresolved Spotify album metadata: {'; '.join(sorted(unknown))}")
    result = {}
    for key, source in grouped.items():
        canonical = [meta[r["track_id"]] for r in source if meta[r["track_id"]]["album_id"] == key]
        if not canonical:
            raise Blocked(f"{key}: no metadata for explicitly mapped canonical album")
        m = canonical[-1]
        credits = {tuple((a["id"], a["name"]) for a in x["album_artists"]) for x in canonical}
        override = overrides.get(key)
        if len(credits) != 1 and not override:
            raise Blocked(f"{key}: inconsistent track lead artists for album; needs verified album_credits.json mapping")
        artists = override["artists"] if override else " | ".join(a["name"] for a in m["album_artists"])
        artist_ids = override["artist_ids"] if override else [a["id"] for a in m["album_artists"]]
        source.sort(key=lambda r:r["date"])
        own_art = next((r["image_url"] for r in reversed(source) if meta[r["track_id"]]["album_id"] == key and r["image_url"]), "")
        release = m.get("album_date", "")
        # A track can chart before its album is released (pre-release single).
        # Song-chart history does not establish an official album-chart debut.
        debut = None
        result[key] = {"key": key, "name": m["album_name"], "artists": artists, "artist_ids": artist_ids,
                       "image_url": own_art, "entry_date": release, "debut": debut, "rows": source,
                       "female_override": override.get("female_solo") if override else None}
    return result


def summarize(item, until, album=False):
    rows = [r for r in item["rows"] if r["date"] <= until]
    if not rows:
        return None
    daily = defaultdict(int)
    for row in rows:
        daily[row["date"]] += row["streams"]
    peak_date = min(daily, key=lambda d:(-daily[d], d))
    debut = item["debut"]
    if debut and debut["date"] > until:
        debut = None
    current = [r for r in rows if r["date"] == until]
    return {**{k:v for k,v in item.items() if k not in {"rows", "debut"}},
            "streams": sum(daily.values()), "days_2026": len(daily),
            "peak_rank_2026": None if album else min(r["rank"] for r in rows),
            "peak_streams_2026": daily[peak_date], "peak_streams_date": peak_date,
            "debut_date": debut["date"] if debut else None,
            "debut_rank": debut["rank"] if debut else None,
            "debut_streams": debut["streams"] if debut else None,
            "debut_before_year": item["entry_date"][:4] < until[:4],
            "still_charting": bool(current), "current_rank": current[0]["rank"] if current and not album else None,
            "current_tracks": len(current) if album else None,
            "last_date": max(daily), "chart_tracks": len({r["track_id"] for r in rows}) if album else None,
            "first_chart_date_2026": min(daily), "first_chart_streams_2026": daily[min(daily)],
            "is_taylor": TAYLOR_ARTIST_ID in item["artist_ids"]}


def female_status(item, genders):
    if item.get("female_override") is not None:
        return item["female_override"]
    lead = next(iter(item["artist_ids"]), "")
    g = genders.get(lead) or {}
    if not g.get("gender") or not g.get("type"):
        return None
    return g["gender"] == "F" and g["type"] == "solo"


def build_rankings(rows, *, region, year, as_of, compare_to, kind, scope, top=15, pools=None, include_keys=()):
    """Rank the entire pool before selecting all Taylor entries or the overall top 15.

    include_keys: entries shown below the selection even outside it (e.g. a new debut
    ranked #22, posted under the top 15), with their real rank."""
    album = kind in {"albums", "female_albums"}
    debut_ranking = kind in {"debuts", "female_debuts"}
    female = kind.startswith("female")
    pool = pools["albums" if album else "songs"] if pools else (album_metrics(rows, year) if album else song_metrics(rows, year))
    genders = read_json(ARTIST_GENDERS_PATH, {})
    metric = "debut_streams" if debut_ranking else "streams"

    def ranking(until):
        known, unknown = {}, {}
        for key, item in pool.items():
            summary = summarize(item, until, album)
            if summary is None or (debut_ranking and summary["debut_streams"] is None):
                continue
            status = female_status(item, genders) if female else True
            if status is None:
                unknown[key] = summary
            elif status:
                known[key] = summary
        ranks = rank({key:s[metric] for key,s in known.items()})
        for key,s in known.items():
            s["rank"] = ranks[key]
        return known, unknown

    now, unknown = ranking(as_of)
    previous, previous_unknown = ranking(compare_to) if compare_to else ({}, {})
    include_keys = set(include_keys)
    selected = sorted((s for s in now.values() if (s["is_taylor"] if scope == "taylor" else s["rank"] <= top)
                       or s["key"] in include_keys),
                      key=lambda s:(s["rank"], s["key"]))
    # An unclassified Taylor entry cannot disappear behind a "no Taylor" skip.
    floor = min((s[metric] for s in selected), default=0)
    hidden_taylor = [s for s in unknown.values() if s["is_taylor"] and (scope == "taylor" or s[metric] >= floor)]
    if hidden_taylor:
        raise Blocked(f"{kind}: unclassified Taylor lead credits prevent verifying the selection: " +
                      "; ".join(s["name"] for s in hidden_taylor))
    if not any(s["is_taylor"] for s in selected):
        return {"skipped": f"{kind}: Taylor has no entries" + (f" in the top {top}" if scope == "all" else "")}
    # Only unknowns that could affect a displayed rank can block it.
    for label, unresolved, known, keys in (("current",unknown,now,[s["key"] for s in selected]),
                                           ("comparison",previous_unknown,previous,[s["key"] for s in selected if s["key"] in previous])):
        if not keys:
            continue
        floor = min(known[key][metric] for key in keys)
        blocking = [s for s in unresolved.values() if s[metric] >= floor]
        if blocking:
            raise Blocked(f"{kind} {label}: unclassified lead artists affect displayed ranks: " +
                          "; ".join(f"{s['name']} / {s['artists']} ({s[metric]:,})" for s in blocking))
    for s in selected:
        before = previous.get(s["key"])
        s["previous_rank"] = before["rank"] if before else None
        s["movement"] = before["rank"] - s["rank"] if before else None
    return {"region":region,"year":year,"kind":kind,"as_of":as_of,"compare_to":compare_to,
            "metric":metric,"entries":selected,"count":len(now),"scope":scope,"top":top if scope=="all" else None,
            "metric_notes": "Albums: sums/days/peaks of charting tracks, no official album chart rank" if album else
                            "Days and both peaks use only this year's daily rows; debut is first-ever chart day in this year"}
