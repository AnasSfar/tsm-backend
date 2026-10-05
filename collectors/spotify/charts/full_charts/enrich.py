#!/usr/bin/env python3
"""Metadata needed by stats.py for the albums / female rankings.

1. Albums: Spotify album of every distinct charting track_id, via the web-player
   GraphQL getTrack (same TokenManager as the streams pipeline; the public Web
   API /v1/tracks answers 429 with our session token). Cached in
   snapshots/spotify_charts_full/meta/tracks.json — a track is fetched once.
2. Lead-artist gender (F / NF) in db/spotify_charts_full/artist_genders.json,
   {artist_id: {name, gender, type, source}}. Seeded from the curated
   artists_global/Artists.csv; the rest only with --classify-genders N (LLM,
   artists_global/artist_gender_llm.py, source "llm:<provider>") for the lead
   artists of each region's top N songs. A wrong entry is fixed by editing the
   JSON and setting source "manual" (never overwritten).

    python collectors/spotify/charts/full_charts/enrich.py                       # every raw region, current year
    python collectors/spotify/charts/full_charts/enrich.py --regions global --classify-genders 300
    python collectors/spotify/charts/full_charts/enrich.py --no-tracks --classify-genders 200
"""
from __future__ import annotations

import argparse
import csv
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    ARTIST_GENDERS_PATH, LEGACY_ARTISTS_CSV, ROOT, TRACKS_META_PATH, raw_regions, read_json, read_rows, write_json,
)

STREAMS_SCRIPTS = ROOT / "collectors" / "spotify" / "streams" / "tools" / "scripts"
ARTISTS_GLOBAL = ROOT / "collectors" / "spotify" / "charts" / "artists_global"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"


# ── albums ───────────────────────────────────────────────────────────────────

def _album_from_track_union(tu: dict) -> dict:
    album = tu.get("albumOfTrack") or {}
    first = ((tu.get("firstArtist") or {}).get("items") or [{}])[0]
    artist_id = (first.get("uri") or "").split(":")[-1]
    artist_name = (first.get("profile") or {}).get("name") or ""
    return {
        "album_id": album.get("id") or (album.get("uri") or "").split(":")[-1],
        "album_name": album.get("name") or "",
        "album_type": (album.get("type") or "").lower(),  # album / single / ep / compilation
        "album_date": (album.get("date") or {}).get("isoString", "")[:10],
        # GraphQL getTrack has no album artists: the track's first artist stands in.
        "album_artists": [{"id": artist_id, "name": artist_name}] if artist_id else [],
    }


def fetch_albums(track_ids: list[str], workers: int, interval: float) -> None:
    for p in (STREAMS_SCRIPTS, STREAMS_SCRIPTS.parents[2], STREAMS_SCRIPTS.parents[3]):
        if str(p) not in sys.path:
            sys.path.append(str(p))
    import spotify_api as sa  # type: ignore

    meta = read_json(TRACKS_META_PATH, {})
    todo = [t for t in track_ids if t not in meta]
    print(f"[ALBUMS] {len(track_ids)} titre(s) distinct(s), {len(todo)} a resoudre")
    if not todo:
        return
    tm = sa.TokenManager()
    if not tm.capture():
        raise SystemExit("[ALBUMS] capture des tokens Spotify impossible")
    lock = threading.Lock()
    pace = threading.Lock()
    state = {"next": 0.0, "done": 0, "fail": 0}
    session = sa._requests.Session()

    def one(tid: str) -> None:
        body = {"variables": {"uri": f"spotify:track:{tid}"}, "operationName": "getTrack",
                "extensions": {"persistedQuery": {"version": 1, "sha256Hash": sa.GETTRACK_HASH}}}
        for attempt in range(6):
            with pace:
                wait = state["next"] - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                state["next"] = time.monotonic() + interval
            tok = tm.get()
            headers = {"Authorization": f"Bearer {tok['bearer']}", "client-token": tok["client_token"],
                       "spotify-app-version": sa.APP_VERSION, "app-platform": "WebPlayer",
                       "Accept": "application/json", "Content-Type": "application/json;charset=UTF-8",
                       "Origin": "https://open.spotify.com", "Referer": "https://open.spotify.com/", "User-Agent": UA}
            try:
                r = session.post(sa.GRAPHQL_URL, json=body, headers=headers, timeout=(5, 20))
            except Exception:
                time.sleep(3)
                continue
            if r.status_code == 200:
                tu = ((r.json() or {}).get("data") or {}).get("trackUnion") or {}
                if tu.get("albumOfTrack"):
                    with lock:
                        meta[tid] = _album_from_track_union(tu)
                        state["done"] += 1
                        if state["done"] % 200 == 0:
                            write_json(TRACKS_META_PATH, meta, indent=None)
                            print(f"[ALBUMS] {state['done']}/{len(todo)}")
                    return
                break  # track not found / unavailable: leave unresolved (stats blocks on it)
            if r.status_code == 401:
                tm.mark_expired()
                tm.capture(force_refresh=True)
                continue
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After") or 0) or 10 * (attempt + 1))
                continue
            time.sleep(5)
        with lock:
            state["fail"] += 1
            print(f"[ALBUMS] echec {tid}")

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, todo))
    finally:
        write_json(TRACKS_META_PATH, meta, indent=None)
    print(f"[ALBUMS] {state['done']} resolu(s), {state['fail']} echec(s) -> {TRACKS_META_PATH.relative_to(ROOT)}")


# ── genders ──────────────────────────────────────────────────────────────────

def seed_genders(genders: dict) -> int:
    added = 0
    if not LEGACY_ARTISTS_CSV.exists():
        return 0
    with LEGACY_ARTISTS_CSV.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            aid, g = (row.get("artist_id") or "").strip(), (row.get("gender") or "").strip()
            if aid and g in {"F", "NF"} and aid not in genders:
                genders[aid] = {"name": row.get("artist_name") or "", "gender": g,
                                "type": row.get("type") or "", "source": "Artists.csv"}
                added += 1
    return added


def lead_artists_of_top(regions: list[str], year: str, top: int) -> dict[str, str]:
    """{lead_artist_id: name} for the top `top` songs (year-to-date chart streams) of each region."""
    out: dict[str, str] = {}
    for region in regions:
        totals: dict[str, int] = {}
        lead: dict[str, tuple[str, str]] = {}
        for row in read_rows(region, year):
            if row["streams"] is None or not row["artist_ids"] or not row["track_name"]:  # wiped-metadata rows
                continue
            totals[row["track_id"]] = totals.get(row["track_id"], 0) + row["streams"]
            lead[row["track_id"]] = (row["artist_ids"].split(" | ")[0], row["artists"].split(" | ")[0])
        for tid in sorted(totals, key=lambda t: -totals[t])[:top]:
            aid, name = lead[tid]
            out.setdefault(aid, name)
    return out


def classify_genders(genders: dict, wanted: dict[str, str], do_llm: bool) -> None:
    missing = {aid: n for aid, n in wanted.items() if aid not in genders}
    print(f"[GENRES] {len(wanted)} artiste(s) principal(aux) concerne(s), {len(missing)} sans genre")
    if not missing or not do_llm:
        for aid, n in list(missing.items())[:40]:
            print(f"  - {n} ({aid})")
        return
    sys.path.insert(0, str(ARTISTS_GLOBAL))
    from artist_gender_llm import classify_artist_gender  # type: ignore

    for i, (aid, name) in enumerate(missing.items(), 1):
        g, t, provider = classify_artist_gender(name)
        if g:
            genders[aid] = {"name": name, "gender": g, "type": t, "source": f"llm:{provider}"}
        print(f"[GENRES] {i}/{len(missing)} {name}: {g or '?'} {t}")
        if i % 25 == 0:
            write_json(ARTIST_GENDERS_PATH, genders)


def run_enrich(regions: list[str] | None, year: str, *, tracks: bool = True, workers: int = 3,
               interval: float = 0.25, classify_top: int = 0, list_top: int = 100) -> int:
    """regions=None -> every region with raw data. Also called by collect.py at the end of a run."""
    regions = raw_regions() if not regions else regions
    regions = [r for r in regions if (ROOT / "db" / "spotify_charts_full" / f"{r}_{year}.csv").exists()]
    print(f"[PLAN] {len(regions)} region(s) avec table {year}")
    if not regions:
        return 0

    if tracks:
        ids: set[str] = set()
        for r in regions:
            ids.update(row["track_id"] for row in read_rows(r, year) if row["track_id"])
        fetch_albums(sorted(ids), workers, interval)

    genders = read_json(ARTIST_GENDERS_PATH, {})
    seeded = seed_genders(genders)
    if seeded:
        print(f"[GENRES] {seeded} artiste(s) repris de Artists.csv")
    top = classify_top or list_top
    try:
        classify_genders(genders, lead_artists_of_top(regions, year, top), do_llm=bool(classify_top))
    finally:
        write_json(ARTIST_GENDERS_PATH, genders)
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Album + lead-artist gender metadata for full_charts/stats.py.")
    p.add_argument("--regions", nargs="+", default=["all"], help="Regions (default: every region with raw data)")
    p.add_argument("--year", default=str(date.today().year))
    p.add_argument("--no-tracks", action="store_true", help="Skip album resolution")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--interval", type=float, default=0.25, help="Min seconds between GraphQL requests (all workers)")
    p.add_argument("--classify-genders", type=int, default=0, metavar="N",
                   help="LLM-classify the lead artists of each region's top N songs (default 0 = only list missing)")
    p.add_argument("--list-genders", type=int, default=100, metavar="N",
                   help="Without --classify-genders: list missing genders for each region's top N (default 100)")
    args = p.parse_args()
    regions = None if args.regions == ["all"] else [r.lower() for r in args.regions]
    return run_enrich(regions, args.year, tracks=not args.no_tracks, workers=args.workers, interval=args.interval,
                      classify_top=args.classify_genders, list_top=args.list_genders)


if __name__ == "__main__":
    raise SystemExit(main())
