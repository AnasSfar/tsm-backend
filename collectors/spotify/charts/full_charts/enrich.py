#!/usr/bin/env python3
"""Metadata needed by stats.py for the albums / female rankings.

1. Albums: Spotify album of every distinct charting track_id, via the web-player
   GraphQL getTrack (the public Web API /v1/tracks answers 429 with our session
   token). Tokens: web_tokens.TokenPool = anonymous token + charts accounts
   except the streams one, never the streams TokenManager/cache. Cached in
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
    import requests
    import spotify_api as sa  # type: ignore  # GraphQL URL + getTrack hash only
    from web_tokens import TokenPool

    meta = read_json(TRACKS_META_PATH, {})
    todo = [t for t in track_ids if t not in meta]
    print(f"[ALBUMS] {len(track_ids)} titre(s) distinct(s), {len(todo)} a resoudre")
    if not todo:
        return
    pool_tokens = TokenPool(interval)
    lock = threading.Lock()
    state = {"done": 0, "fail": 0, "started": time.monotonic()}
    local = threading.local()

    def one(tid: str) -> None:
        if not hasattr(local, "session"):
            local.session = requests.Session()
        body = {"variables": {"uri": f"spotify:track:{tid}"}, "operationName": "getTrack",
                "extensions": {"persistedQuery": {"version": 1, "sha256Hash": sa.GETTRACK_HASH}}}
        for attempt in range(8):
            i, tok = pool_tokens.acquire()
            headers = {"Authorization": f"Bearer {tok['bearer']}", "client-token": tok["client_token"],
                       "spotify-app-version": tok.get("app_version") or sa.APP_VERSION, "app-platform": "WebPlayer",
                       "Accept": "application/json", "Content-Type": "application/json;charset=UTF-8",
                       "Origin": "https://open.spotify.com", "Referer": "https://open.spotify.com/", "User-Agent": UA}
            try:
                r = local.session.post(sa.GRAPHQL_URL, json=body, headers=headers, timeout=(5, 20))
            except Exception:
                time.sleep(3)
                continue
            if r.status_code == 200:
                pool_tokens.mark_ok(i)
                tu = ((r.json() or {}).get("data") or {}).get("trackUnion") or {}
                if tu.get("albumOfTrack"):
                    with lock:
                        meta[tid] = _album_from_track_union(tu)
                        state["done"] += 1
                        if state["done"] % 500 == 0:
                            write_json(TRACKS_META_PATH, meta, indent=None)
                            rate = state["done"] / max(1.0, time.monotonic() - state["started"])
                            print(f"[ALBUMS] {state['done']}/{len(todo)} ({rate:.1f}/s, "
                                  f"reste ~{(len(todo) - state['done']) / max(rate, 0.1) / 60:.0f} min)")
                    return
                break  # track not found / unavailable: leave unresolved (stats blocks on it)
            if r.status_code == 401:
                pool_tokens.refresh(i, tok["bearer"])
                continue
            if r.status_code == 429:
                pool_tokens.mark_429(i, float(r.headers.get("Retry-After") or 0) or 10 * (attempt + 1))
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


def lead_artists_of_top(regions: list[str], year: str, top: int | None) -> dict[str, str]:
    """{lead_artist_id: name} for the top `top` songs (year-to-date chart streams) of each region
    (top=None: every song, i.e. everything the All Artists page shows). Most-streamed artists
    first, so an LLM run that stops early has done the most visible ones."""
    out: dict[str, str] = {}
    best: dict[str, int] = {}
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
            best[aid] = max(best.get(aid, 0), totals[tid])
    return {aid: out[aid] for aid in sorted(out, key=lambda a: -best[a])}


LLM_MAX_CONSECUTIVE_FAILURES = 15  # provider down / rate-limited: stop instead of hammering it for hours


def save_genders(genders: dict, before: set[str]) -> None:
    """Write only the entries added by this run, merged into the file as it is NOW:
    a manual edit or a merge made while this run was going is never overwritten."""
    on_disk = read_json(ARTIST_GENDERS_PATH, {})
    for aid in set(genders) - before:
        on_disk.setdefault(aid, genders[aid])
    write_json(ARTIST_GENDERS_PATH, on_disk)


WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_UA = "tsm-backend full_charts/enrich.py (Spotify charts metadata; github.com/AnasSfar/tsm-backend)"
# P21 (sex or gender) -> our gender. Anything else (non-binary, ...) stays blank.
WD_GENDER = {"Q6581072": "F", "Q1052281": "F", "Q6581097": "NF", "Q2449503": "NF"}  # female, trans woman, male, trans man
WD_HUMAN, WD_GIRL_GROUP, WD_BOY_BAND = "Q5", "Q641066", "Q216337"


def wikidata_genders(artist_ids: list[str], batch: int = 200) -> dict[str, tuple[str, str]]:
    """{spotify_artist_id: (gender, type)} from Wikidata (P1902 = Spotify artist ID): people
    with an unambiguous P21, girl groups (F GROUP) and boy bands (NF GROUP). Other groups and
    anything ambiguous are left out (blank beats wrong). Network errors -> partial result."""
    import requests

    out: dict[str, tuple[str, str]] = {}
    for start in range(0, len(artist_ids), batch):
        chunk = artist_ids[start:start + batch]
        query = ("SELECT ?sid ?gender ?inst WHERE { VALUES ?sid { %s } ?item wdt:P1902 ?sid . "
                 "OPTIONAL { ?item wdt:P21 ?gender } OPTIONAL { ?item wdt:P31 ?inst } }"
                 % " ".join(f'"{a}"' for a in chunk))
        try:
            r = requests.post(WIKIDATA_SPARQL, data={"query": query, "format": "json"},
                              headers={"User-Agent": WIKIDATA_UA, "Accept": "application/sparql-results+json"}, timeout=90)
            r.raise_for_status()
            bindings = r.json()["results"]["bindings"]
        except Exception as exc:
            print(f"[WIKIDATA] echec lot {start // batch + 1}: {exc!r} - ignore")
            continue
        facts: dict[str, dict[str, set[str]]] = {}
        for b in bindings:
            f = facts.setdefault(b["sid"]["value"], {"gender": set(), "inst": set()})
            for key in ("gender", "inst"):
                if key in b:
                    f[key].add(b[key]["value"].rsplit("/", 1)[-1])
        for sid, f in facts.items():
            if WD_HUMAN in f["inst"]:
                mapped = {WD_GENDER.get(q) for q in f["gender"]}
                if len(mapped) == 1 and None not in mapped:
                    out[sid] = (mapped.pop(), "solo")
            elif f["inst"] & {WD_GIRL_GROUP, WD_BOY_BAND} and not (WD_GIRL_GROUP in f["inst"] and WD_BOY_BAND in f["inst"]):
                out[sid] = ("F" if WD_GIRL_GROUP in f["inst"] else "NF", "GROUP")
        time.sleep(1)  # be gentle with the public endpoint
    return out


def classify_genders(genders: dict, wanted: dict[str, str], do_llm: bool, save=lambda: None) -> None:
    missing = {aid: n for aid, n in wanted.items() if aid not in genders}
    print(f"[GENRES] {len(wanted)} artiste(s) principal(aux) concerne(s), {len(missing)} sans genre")
    if missing:
        # Sourced data first (Wikidata), the LLM only for what is left.
        found = wikidata_genders(list(missing))
        for aid, (g, t) in found.items():
            genders[aid] = {"name": missing.pop(aid), "gender": g, "type": t, "source": "wikidata"}
        print(f"[WIKIDATA] {len(found)} artiste(s) classe(s), {len(missing)} restant(s)")
        if found:
            save()
    if not missing or not do_llm:
        for aid, n in list(missing.items())[:40]:
            print(f"  - {n} ({aid})")
        return
    sys.path.insert(0, str(ARTISTS_GLOBAL))
    from artist_gender_llm import classify_artist_gender  # type: ignore

    failures = 0
    for i, (aid, name) in enumerate(missing.items(), 1):
        g, t, provider = classify_artist_gender(name)
        if g:
            genders[aid] = {"name": name, "gender": g, "type": t, "source": f"llm:{provider}"}
        failures = 0 if provider else failures + 1  # provider "" = no LLM answered (down / 429)
        print(f"[GENRES] {i}/{len(missing)} {name}: {g or '?'} {t}")
        if i % 25 == 0:
            save()
        if failures >= LLM_MAX_CONSECUTIVE_FAILURES:
            print(f"[GENRES] LLM indisponible ({failures} echecs d'affilee) - arret ; "
                  f"{len(missing) - i} artiste(s) restent sans genre (masques dans les filtres F/M), nouvel essai au prochain run")
            break


def run_enrich(regions: list[str] | None, year: str, *, tracks: bool = True, workers: int = 8,
               interval: float = 0.15, classify_top: int = 0, list_top: int = 100) -> int:
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
    before = set(genders)
    seeded = seed_genders(genders)
    if seeded:
        print(f"[GENRES] {seeded} artiste(s) repris de Artists.csv")
    # classify_top < 0 = every song on the page (default of the daily run), > 0 = top N, 0 = list only.
    top = None if classify_top < 0 else (classify_top or list_top)
    try:
        classify_genders(genders, lead_artists_of_top(regions, year, top), do_llm=bool(classify_top),
                         save=lambda: save_genders(genders, before))
    finally:
        save_genders(genders, before)
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Album + lead-artist gender metadata for full_charts/stats.py.")
    p.add_argument("--regions", nargs="+", default=["all"], help="Regions (default: every region with raw data)")
    p.add_argument("--year", default=str(date.today().year))
    p.add_argument("--no-tracks", action="store_true", help="Skip album resolution")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--interval", type=float, default=0.15,
                   help="Min seconds between GraphQL requests PER TOKEN (default 0.15; x1.5 per token on each 429)")
    p.add_argument("--classify-genders", type=int, default=0, metavar="N",
                   help="LLM-classify the lead artists of each region's top N songs (-1 = every song; default 0 = only list missing)")
    p.add_argument("--list-genders", type=int, default=100, metavar="N",
                   help="Without --classify-genders: list missing genders for each region's top N (default 100)")
    args = p.parse_args()
    regions = None if args.regions == ["all"] else [r.lower() for r in args.regions]
    return run_enrich(regions, args.year, tracks=not args.no_tracks, workers=args.workers, interval=args.interval,
                      classify_top=args.classify_genders, list_top=args.list_genders)


if __name__ == "__main__":
    raise SystemExit(main())
