#!/usr/bin/env python3
"""Local, no-post YTD ranking cards in the Spotify album-page card design (one table, many columns).

python collectors/spotify/charts/full_charts/generate_ranking_images.py --kind female songs
python collectors/spotify/charts/full_charts/generate_ranking_images.py --kind albums female_albums --scope both
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
from datetime import date
import gzip
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import (COVERS_DIR, CSV_FIELDS, ROOT,
                    date_range, entries_to_rows, raw_dates, raw_path, read_rows, region_title)
import stats
from ranking_metrics import KINDS, album_metrics, song_metrics, build_rankings

sys.path.insert(0, str(ROOT / "collectors"))
from comp.spotify_ranking_page import build_html, header_tone, render
from comp.milestone_card import milestone_palette


def verify_source(region: str, year: str, as_of: str) -> list[dict]:
    """Compare the derived table with every exact raw chart in the requested period."""
    stats.check_coverage(region, year, as_of)
    try:
        rows = [r for r in read_rows(region, year) if r["date"] <= as_of]
    except (ValueError, KeyError) as exc:
        raise stats.Blocked(f"{region} {year}: malformed derived chart CSV: {exc}") from exc
    if any(r["date"][:4] != year for r in rows):
        raise stats.Blocked(f"{region} {year}: derived CSV contains rows from another year")
    by_day = defaultdict(list)
    for row in rows:
        by_day[row["date"]].append(row)
    def identity(row):
        return tuple(str(row.get(k) if row.get(k) is not None else "") for k in CSV_FIELDS)
    for day in date_range(f"{year}-01-01", as_of):
        with gzip.open(raw_path(region, day), "rt", encoding="utf-8") as fh:
            raw = entries_to_rows(day, json.load(fh))
        ranks = [r["rank"] for r in raw]
        if len(raw) != 200 or any(not isinstance(r, int) for r in ranks) or sorted(ranks) != list(range(1, 201)):
            raise stats.Blocked(f"{region} {day}: invalid raw chart ranks / row count ({len(raw)})")
        if any(not r["track_id"] or not isinstance(r["streams"], int) or r["streams"] < 0 for r in raw):
            raise stats.Blocked(f"{region} {day}: missing track ID or exact stream count")
        if len({r['track_id'] for r in raw}) != len(raw):
            raise stats.Blocked(f"{region} {day}: duplicate raw track IDs")
        if Counter(map(identity, raw)) != Counter(map(identity, by_day[day])):
            raise stats.Blocked(f"{region} {day}: CSV differs from the raw Spotify snapshot; rebuild the CSV")
    return rows


def artwork(entries: list[dict]) -> list[dict]:
    """Use the exact cached cover already identified by the verified metrics."""
    output = []
    for entry in entries:
        url = entry["image_url"]
        path = COVERS_DIR / f"{url.rsplit('/', 1)[-1][-24:]}.jpg" if url else None
        if not path or not path.is_file():
            raise stats.Blocked(f"{entry['key']} ({entry['name']}): exact cached cover missing; collect covers first")
        data = path.read_bytes()
        output.append({**entry, "art": f"data:image/jpeg;base64,{base64.b64encode(data).decode()}",
                       "tone": header_tone(milestone_palette(data)["accent"])})
    return output


def generate(*, region="global", year="2026", kinds=KINDS, as_of=None,
             compare_to=None, top=15, scope="taylor", output_dir: Path) -> int:
    dates = raw_dates(region, year)
    if not dates:
        raise stats.Blocked(f"{region} {year}: no raw charts")
    as_of = as_of or dates[-1]
    if as_of not in dates or as_of[:4] != year:
        raise stats.Blocked(f"{region}: requested chart date {as_of} is unavailable")
    previous = compare_to or next((d for d in reversed(dates) if d < as_of), None)
    if previous and (previous not in dates or previous >= as_of):
        raise stats.Blocked(f"Invalid comparison date {previous}; it must be an earlier chart in {year}")
    source_rows = verify_source(region, year, as_of)
    pools = {"songs": song_metrics(source_rows, year)}
    errors, manifest = [], []
    scopes = ("taylor", "all") if scope == "both" else (scope,)
    for kind in kinds:
        for active_scope in scopes:
            try:
                if kind in {"albums", "female_albums"} and "albums" not in pools:
                    pools["albums"] = album_metrics(source_rows, year)
                result = build_rankings(source_rows, region=region, year=year, as_of=as_of,
                                        compare_to=previous, kind=kind, scope=active_scope, top=top, pools=pools)
                if result.get("skipped"):
                    print(f"[SKIP] {active_scope}: {result['skipped']}")
                    manifest.append({"kind":kind,"scope":active_scope,"status":"skipped","reason":result['skipped']})
                    continue
                selected = result["entries"]
                entries = artwork(selected)
                # One table per image (up to 15 rows, like a top 15); longer Taylor
                # lists paginate without losing a row, original ranks intact.
                pages = [entries[i:i + 15] for i in range(0, len(entries), 15)]
                selection = "taylor-all-entries" if active_scope == "taylor" else "overall-top15"
                slug = f"spotify-charts-{region}-{year}-{kind}-{selection}-{as_of}"
                paths = []
                for i, page in enumerate(pages, 1):
                    suffix = f"-{i}" if len(pages) > 1 else ""
                    out = output_dir / f"{slug}{suffix}.png"
                    doc = build_html(result, page, region_name=region_title(region, year), scope=active_scope,
                                     top=top, page_number=i, page_count=len(pages))
                    paths.append(render(doc, out))
                audit = {**result, "source_verified": "CSV matched against each daily raw snapshot",
                         "images": [p.name for p in paths]}
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / f"{slug}.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
                manifest.append({"kind":kind,"scope":active_scope,"status":"ready","entries":len(selected),"images":[p.name for p in paths]})
                for path in paths:
                    print(f"[PREVIEW] {path}")
            except stats.Blocked as exc:
                reason = f"{kind} {active_scope}: {exc}"
                errors.append(reason)
                manifest.append({"kind":kind,"scope":active_scope,"status":"blocked","reason":str(exc)})
                print(f"[BLOCKED] {reason}")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "latest-manifest.json").write_text(json.dumps({"as_of":as_of,"compare_to":previous,"rankings":manifest},ensure_ascii=False,indent=2),encoding="utf-8")
    if errors:
        (output_dir / f"blocked-{region}-{year}-{as_of}.log").write_text("\n".join(errors) + "\n", encoding="utf-8")
    return 2 if errors else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--region", default="global")
    p.add_argument("--year", default=str(date.today().year))
    p.add_argument("--kind", nargs="+", choices=KINDS, default=list(KINDS))
    p.add_argument("--as-of")
    p.add_argument("--compare-to")
    p.add_argument("--top", type=int, choices=(15,), default=15, help="Overall charts show the top 15; Taylor scope has no rank limit")
    p.add_argument("--scope", choices=("taylor", "all", "both"), default="both")
    p.add_argument("--output-dir", type=Path, default=ROOT / "previews_and_sims" / "spotify-chart-rankings" / "latest")
    args = p.parse_args()
    if args.top < 1:
        p.error("--top must be positive")
    try:
        return generate(region=args.region, year=args.year, kinds=args.kind, as_of=args.as_of,
                        compare_to=args.compare_to, top=args.top, scope=args.scope, output_dir=args.output_dir)
    except (stats.Blocked, FileNotFoundError) as exc:
        print(f"[BLOCKED] {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
