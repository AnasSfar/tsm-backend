#!/usr/bin/env python3
"""Per-song aggregates of the full Spotify daily top 200 (every artist) for the
frontend Spotify Charts "All Artists" page, one file per region and year.

Same shape as the History view (`/api/charts/discography`): `songs` + a stable
"yesterday" baseline `previous_songs` (the region's latest chart date
excluded), so the page computes rank movement under any sort column.

Per song (track ids merged exactly like stats.py — canonical_ids):
  last_date / last_rank / last_streams   last day on the chart this year
  peak_rank / peak_date / total_days     Spotify's OWN all-time values (chartEntryData
                                         peakRank / peakDate / appearancesOnChart, as of last_date)
  peak_streams / peak_streams_date       best day this year
  total_streams / total_streams_days     sum of this year's chart days (outside top 200 = 0)
  debut_date / debut_rank / debut_streams  first day ever on the chart (Spotify entryDate), only
                                         when it is in this year (else empty / 0)
  longest_streak / longest_streak_active longest run that ENDED in this year, length =
                                         Spotify's consecutiveAppearances at the run's last day
                                         (so a run started before Jan 1 keeps its true length)

Output: runtime web export dir data/charts_full/ (local) + R2 charts-full/
(gzipped, upload only when changed). index.json lists regions/years/latest dates.

    python collectors/spotify/charts/full_charts/export.py                 # every region, current year, + R2
    python collectors/spotify/charts/full_charts/export.py --regions global --no-upload
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import ARTIST_GENDERS_PATH, ROOT, TAYLOR_ARTIST_ID, TRACKS_META_PATH, raw_regions, read_json, read_rows, region_title  # noqa: E402
from stats import Blocked, canonical_ids, check_coverage  # noqa: E402

sys.path.insert(0, str(ROOT / "collectors" / "spotify"))
sys.path.insert(0, str(ROOT / "scripts"))
from core.data_paths import WEB_EXPORT_DATA_DIR  # noqa: E402

OUT_DIR = WEB_EXPORT_DATA_DIR / "charts_full"


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _next_day(d: str) -> str:
    return (date.fromisoformat(d) + timedelta(days=1)).isoformat()


def aggregate(rows: list[dict], canon: dict[str, str], meta: dict, genders: dict, *,
              until: str | None = None) -> list[dict]:
    by_song: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if until and r["date"] > until:
            continue
        if r["track_id"] and r["streams"] is not None:
            by_song[canon.get(r["track_id"], r["track_id"])].append(r)
    latest = max((r["date"] for rs in by_song.values() for r in rs), default="")

    songs = []
    for tid, rs in by_song.items():
        rs.sort(key=lambda r: (r["date"], -(r["streams"] or 0)))
        # One row per date (two merged ids never chart the same day, but be safe).
        per_day: dict[str, dict] = {}
        for r in rs:
            per_day.setdefault(r["date"], r)
        days = sorted(per_day)
        last = per_day[days[-1]]
        named = next((per_day[d] for d in reversed(days) if per_day[d]["track_name"]), last)
        own = next((per_day[d] for d in reversed(days) if per_day[d]["track_id"] == tid), named)
        best = max(per_day.values(), key=lambda r: (r["streams"] or 0, r["date"]))

        # Debut = the song's first day EVER on this chart (Spotify entryDate). Only
        # when it falls in the exported year: that day is then in our data, and
        # Spotify must agree it was day 1 (appearancesOnChart == 1).
        entry_date = last["entry_date"] or ""
        debut = per_day.get(entry_date) if entry_date[:4] == days[0][:4] else None
        if debut is not None and _int(debut["days_on_chart"]) != 1:
            debut = None

        # Runs of consecutive chart days; true length = Spotify streak at run end.
        longest, longest_active = 0, False
        run_end = None
        for i, d in enumerate(days):
            if i + 1 == len(days) or days[i + 1] != _next_day(d):
                run_end = per_day[d]
                length = _int(run_end["streak"]) or 1
                active = d == latest
                if length > longest or (length == longest and active):
                    longest, longest_active = length, active

        artist_ids = (named["artist_ids"] or "").split(" | ") if named["artist_ids"] else []
        album = meta.get(own["track_id"]) or meta.get(tid) or {}
        songs.append({
            "track_id": tid,
            "song_name": named["track_name"],
            "artist_name": (named["artists"] or "").replace(" | ", ", "),
            # Separate lists (names can contain commas: "Tyler, The Creator") for the page's artist filter.
            "artist_ids": artist_ids,
            "artist_names": (named["artists"] or "").split(" | ") if named["artists"] else [],
            "album_name": album.get("album_name") or "",
            "image_url": named["image_url"] or "",
            "is_taylor": TAYLOR_ARTIST_ID in artist_ids,
            # Lead (first credited) artist: F / NF and solo / GROUP, "" = not classified
            # yet (enrich.py) — the page never ranks past an unclassified song.
            "lead_gender": (genders.get(artist_ids[0]) or {}).get("gender", "") if artist_ids else "",
            "lead_type": (genders.get(artist_ids[0]) or {}).get("type", "") if artist_ids else "",
            "last_date": days[-1],
            "last_rank": last["rank"] or 0,
            "last_streams": last["streams"] or 0,
            "peak_rank": _int(last["peak_rank"]),
            "peak_date": last["peak_date"] or "",
            "total_days": _int(last["days_on_chart"]),
            "peak_streams": best["streams"] or 0,
            "peak_streams_date": best["date"],
            "total_streams": sum(r["streams"] or 0 for r in per_day.values()),
            "total_streams_days": len(days),
            "debut_date": debut["date"] if debut else "",
            "debut_rank": (debut["rank"] or 0) if debut else 0,
            "debut_streams": (debut["streams"] or 0) if debut else 0,
            "longest_streak": longest,
            "longest_streak_active": longest_active,
        })
    songs.sort(key=lambda s: (-s["total_streams"], s["track_id"]))
    return songs


PREVIOUS_FIELDS = ("track_id", "song_name", "lead_gender", "lead_type", "last_date", "last_rank", "last_streams",
                   "debut_streams", "peak_rank",
                   "peak_streams", "total_streams", "total_days", "longest_streak")


def build_region(region: str, year: str, meta: dict, genders: dict) -> dict | None:
    try:
        rows = read_rows(region, year)
    except FileNotFoundError:
        return None
    if not rows:
        return None
    latest = max(r["date"] for r in rows)
    # Never publish a half-collected region (a catch-up in progress): every
    # chart date from Jan 1 to its latest date must be there.
    try:
        check_coverage(region, year, latest)
    except Blocked as exc:
        print(f"[SKIP] {exc}")
        return None
    canon = canonical_ids(rows)
    previous_until = max((r["date"] for r in rows if r["date"] < latest), default=None)
    songs = aggregate(rows, canon, meta, genders)
    previous = aggregate(rows, canon, meta, genders, until=previous_until) if previous_until else []
    return {
        "region": region,
        "region_name": region_title(region, year),
        "year": int(year),
        "latest_date": latest,
        "previous_date": previous_until or "",
        "chart_days": len({r["date"] for r in rows}),
        "songs": songs,
        "previous_songs": [{k: s[k] for k in PREVIOUS_FIELDS} for s in previous],
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Export All Artists page data (full top 200 aggregates) + R2 upload.")
    p.add_argument("--regions", nargs="+", default=["all"], help="Regions (default: every region with raw data)")
    p.add_argument("--year", default=str(date.today().year))
    p.add_argument("--no-upload", action="store_true", help="Write local files only")
    p.add_argument("--dry-run", action="store_true", help="R2: show what would be uploaded")
    args = p.parse_args()
    return run_export(None if args.regions == ["all"] else [r.lower() for r in args.regions], args.year,
                      upload=not args.no_upload, dry_run=args.dry_run)


def run_export(regions: list[str] | None, year: str, *, upload: bool = True, dry_run: bool = False) -> int:
    meta = read_json(TRACKS_META_PATH, {})
    genders = read_json(ARTIST_GENDERS_PATH, {})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payloads: dict[str, bytes] = {}
    for region in regions or raw_regions():
        payload = build_region(region, year, meta, genders)
        if payload is None:
            continue
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        (OUT_DIR / f"{region}_{year}.json").write_bytes(data)
        payloads[f"{region}_{year}.json"] = data
        print(f"[EXPORT] {region} {year}: {len(payload['songs'])} titres, au {payload['latest_date']} ({len(data) // 1024} Ko)")

    # Index over every exported file on disk (all regions/years, not just this run).
    index: dict[str, dict] = {}
    for f in sorted(OUT_DIR.glob("*_*.json")):
        region, _, yr = f.stem.rpartition("_")
        if not yr.isdigit():
            continue
        head = json.loads(f.read_bytes())
        entry = index.setdefault(region, {"key": region, "name": head.get("region_name") or region, "years": {}})
        entry["years"][yr] = {"latest_date": head.get("latest_date", ""), "songs": len(head.get("songs", []))}
    index_payload = {"regions": sorted(index.values(), key=lambda e: (e["key"] != "global", e["name"]))}
    index_bytes = json.dumps(index_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    (OUT_DIR / "index.json").write_bytes(index_bytes)
    payloads["index.json"] = index_bytes
    print(f"[EXPORT] index: {len(index)} region(s) -> {OUT_DIR}")

    if not upload:
        return 0
    import r2  # scripts/r2.py
    import r2_keys

    ok, reason = r2._r2_ready()
    if not ok:
        print(f"[R2] skipped - {reason}")
        return 1
    client = r2.get_s3_client()
    bucket = r2.os.getenv("R2_BUCKET", "taylor-data")
    changed = 0
    for name, data in payloads.items():
        changed += bool(r2.upload_raw_if_changed(
            client=client, bucket=bucket, key=f"{r2_keys.CHARTS_FULL_PREFIX}/{name}", data=data,
            content_type="application/json; charset=utf-8", dry_run=dry_run))
    print(f"[R2] {changed}/{len(payloads)} fichier(s) envoye(s) ({bucket}/{r2_keys.CHARTS_FULL_PREFIX}/)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
