#!/usr/bin/env python3
"""
generate_filtered_artist_chart.py — génère et poste une variante filtrée du
Global Artist Chart Spotify (ex: Top Female Artists, artistes dont le nom
commence par "T", chart artiste propre aux US/UK), centrée sur Taylor Swift
comme le chart de base.

Deux familles de filtres :
  - locaux : filtrent + re-classent la liste d'artistes du chart GLOBAL déjà
    collectée (`config.predicate`), aucun appel réseau.
  - régionaux : vont chercher en direct le chart artiste propre à un pays
    Spotify (`config.region`, ex "us" -> `artist-us-daily`), un classement
    totalement différent du chart global.

Le chart de base (`generate_artist_chart_image.py`) est réutilisé tel quel
pour le rendu HTML/CSS et la génération d'image dans les deux cas — seule la
liste d'artistes en entrée change.

Usage :
    python generate_filtered_artist_chart.py female 2026-08-16
    python generate_filtered_artist_chart.py starts_with_t 2026-08-16 --no-post
    python generate_filtered_artist_chart.py us_artist_chart 2026-08-16
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import generate_artist_chart_image as base  # noqa: E402

if str(base.REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(base.REPO_ROOT))

from collectors.spotify.charts.artists_global.artist_global_daily import (  # noqa: E402
    _fetch_chart,
    _get_bearer_token,
)

ARTISTS_CSV = base.ARTISTS_GLOBAL / "Artists.csv"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ── Filter registry ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FilterConfig:
    label: str            # e.g. "female artists on Spotify Charts" — used in top5/10 tweets
    card_title: str       # e.g. "Taylor Swift · Female Artist Chart"
    rank_scope: str       # e.g. "among female artists on Spotify Charts" — card + solo tweet
    cadence: str           # "daily": post on any subset rank/list change. "rank_up": only on improvement.
    # Local filters (filter+re-rank the already-collected global chart):
    predicate: Callable[[dict, dict[str, str]], bool] | None = None
    needs_gender: bool = False
    # Regional filters (live fetch of a country-specific artist chart, e.g. "us" -> artist-us-daily):
    region: str | None = None


def _is_female(artist: dict, genders: dict[str, str]) -> bool:
    return genders.get(artist.get("artist_id") or "") == "F"


def _starts_with_t(artist: dict, genders: dict[str, str]) -> bool:
    return str(artist.get("artist_name") or "").strip().upper().startswith("T")


def _named_taylor(artist: dict, genders: dict[str, str]) -> bool:
    name = str(artist.get("artist_name") or "").strip().lower()
    return "taylor" in re.findall(r"[a-z]+", name)


FILTERS: dict[str, FilterConfig] = {
    "female": FilterConfig(
        label="female artists on Spotify Charts",
        card_title="Taylor Swift · Female Artist Chart",
        rank_scope="among female artists on Spotify Charts",
        predicate=_is_female,
        cadence="daily",
        needs_gender=True,
    ),
    "starts_with_t": FilterConfig(
        label='artists starting with "T" on Spotify Charts',
        card_title='Taylor Swift · "T" Artist Chart',
        rank_scope='among artists starting with "T" on Spotify Charts',
        predicate=_starts_with_t,
        cadence="rank_up",
    ),
    "named_taylor": FilterConfig(
        label='artists named "Taylor" on Spotify Charts',
        card_title='Taylor Swift · "Taylor" Artist Chart',
        rank_scope='among artists named "Taylor" on Spotify Charts',
        predicate=_named_taylor,
        cadence="rank_up",
    ),
    "us_artist_chart": FilterConfig(
        label="artists on the US Artist Chart",
        card_title="Taylor Swift · US Artist Chart",
        rank_scope="on the US Artist Chart",
        cadence="rank_up",
        region="us",
    ),
    "uk_artist_chart": FilterConfig(
        label="artists on the UK Artist Chart",
        card_title="Taylor Swift · UK Artist Chart",
        rank_scope="on the UK Artist Chart",
        cadence="rank_up",
        region="gb",  # Spotify's own region code for the UK (matches worldwide/daily.py)
    ),
}


# ── Regional chart fetch (live, no local snapshot) ────────────────────────────

def fetch_region_chart(region: str, stats_date: str) -> list[dict] | None:
    token = _get_bearer_token()
    rows, _detected_date, status = _fetch_chart(f"artist-{region}-daily", stats_date, token)
    if status != "HTTP 200" or not rows:
        print(f"[WARN] Regional artist chart '{region}' unavailable for {stats_date}: {status}")
        return None
    return rows


# ── Artists.csv (gender lookup) ───────────────────────────────────────────────

def load_gender_map() -> dict[str, str]:
    genders: dict[str, str] = {}
    if not ARTISTS_CSV.exists():
        return genders
    with ARTISTS_CSV.open("r", newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            artist_id = (row.get("artist_id") or "").strip()
            gender = (row.get("gender") or "").strip()
            if artist_id and gender:
                genders[artist_id] = gender
    return genders


def _append_missing_artists(artists: list[dict]) -> None:
    """Keep Artists.csv complete: add any newly-charting artist with a blank
    gender so it only needs to be filled in once, never re-seeded by hand."""
    known_ids: set[str] = set()
    rows: list[list[str]] = []
    if ARTISTS_CSV.exists():
        with ARTISTS_CSV.open("r", newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            header = next(reader, ["artist_id", "artist_name", "gender"])
            for row in reader:
                if row:
                    known_ids.add(row[0])
                rows.append(row)
    else:
        header = ["artist_id", "artist_name", "gender"]

    new_rows = []
    for a in artists:
        artist_id = a.get("artist_id") or ""
        name = a.get("artist_name") or ""
        if artist_id and artist_id not in known_ids:
            known_ids.add(artist_id)
            new_rows.append([artist_id, name, ""])
    if not new_rows:
        return
    with ARTISTS_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
        writer.writerows(new_rows)
    print(f"[INFO] Artists.csv: {len(new_rows)} nouvel(s) artiste(s) ajoute(s) (gender a completer).")


def _warn_unclassified(rows: list[dict], genders: dict[str, str], *, limit: int = 50) -> None:
    missing = [
        r["artist_name"] for r in rows
        if r["rank"] <= limit and not genders.get(r.get("artist_id") or "")
    ]
    if missing:
        print(f"[WARN] {len(missing)} artiste(s) du top {limit} sans gender dans Artists.csv: {', '.join(missing)}")


# ── Filtering / re-ranking ─────────────────────────────────────────────────────

def build_filtered_rows(artists: list[dict], predicate: Callable[[dict, dict[str, str]], bool], genders: dict[str, str]) -> list[dict]:
    matched = [a for a in artists if base._int_value(a.get("rank")) is not None and predicate(a, genders)]
    matched.sort(key=lambda a: base._int_value(a["rank"]))
    rows: list[dict] = []
    for i, a in enumerate(matched, start=1):
        row = dict(a)
        row["rank"] = i
        row["previous_rank"] = None
        rows.append(row)
    return rows


def attach_previous_ranks(rows: list[dict], previous_full: list[dict] | None, predicate: Callable[[dict, dict[str, str]], bool], genders: dict[str, str]) -> None:
    if not previous_full:
        return
    previous_rows = build_filtered_rows(previous_full, predicate, genders)
    previous_by_id = {base._artist_identity(r): r["rank"] for r in previous_rows}
    for row in rows:
        row["previous_rank"] = previous_by_id.get(base._artist_identity(row))


def _previous_stats_date(stats_date: str) -> str:
    d = datetime.strptime(stats_date, "%Y-%m-%d").date()
    return (d - timedelta(days=1)).isoformat()


def _load_previous_chart(stats_date: str, period: str) -> list[dict] | None:
    try:
        data = base.load_chart(_previous_stats_date(stats_date), period)
    except FileNotFoundError:
        return None
    return data.get("artists") or []


# ── Cadence gating ─────────────────────────────────────────────────────────────

def _should_skip(mode: str, ts_row: dict, rows: list[dict], cadence: str) -> tuple[bool, str]:
    rank = ts_row["rank"]
    # Spotify uses -1 (not None) as its own "no previous rank" sentinel on
    # live-fetched regional charts — normalize it the same way as
    # generate_artist_chart_image.top_list_unchanged does for the global chart.
    previous_rank = base._int_value(ts_row.get("previous_rank"))
    if previous_rank is not None and previous_rank <= 0:
        previous_rank = None

    if cadence == "rank_up":
        improved = previous_rank is not None and rank < previous_rank
        if improved:
            return False, ""
        return True, f"Taylor Swift subset rank not improved (#{previous_rank or '—'} -> #{rank})"

    unchanged = base.rank_unchanged(ts_row)
    if mode == "solo":
        skip = unchanged is not None
    else:
        limit = 5 if mode == "top5" else 10
        skip = unchanged is not None and base.top_list_unchanged(rows, limit)
    detail = f"Taylor Swift rank unchanged (#{unchanged})" if skip else ""
    return skip, detail


# ── Tweet text ─────────────────────────────────────────────────────────────────

def build_tweet(mode: str, stats_date: str, config: FilterConfig) -> str:
    day_fmt = datetime.strptime(stats_date, "%Y-%m-%d").strftime("%A, %B %d, %Y")
    if mode == "top10":
        return f"📈 | The top 10 most streamed {config.label} on {day_fmt} :"
    if mode == "top5":
        return f"📈 | The top 5 most streamed {config.label} on {day_fmt} :"
    return f"📈 | Taylor Swift {config.rank_scope} on {day_fmt} :"


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a filtered Spotify Global Artist Chart card (e.g. Female Artists).")
    parser.add_argument("filter_key", choices=sorted(FILTERS), help="Which filtered chart to generate.")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD (default: latest available)")
    parser.add_argument("--no-post", action="store_true", help="Generate image but skip Twitter posting")
    parser.add_argument("--session", help="Path to a Twitter session JSON file (overrides default)")
    parser.add_argument("--force", action="store_true", help="Ignore the posted lock and post again")
    args = parser.parse_args()

    config = FILTERS[args.filter_key]
    period = "daily"
    stats_date = args.date or base.find_latest_date(period)
    print(f"Filter: {args.filter_key} | Date: {stats_date}")

    if config.region:
        # Regional filter: Taylor's rank comes from that country's own artist
        # chart (fetched live), not from a filter of the global chart — a
        # different ranking entirely. previous_rank is already populated by
        # Spotify itself on each row, no local day-over-day diff needed.
        rows = fetch_region_chart(config.region, stats_date)
        if not rows:
            print(f"No '{args.filter_key}' chart data for {stats_date} — skipping.")
            sys.exit(0)
        ts_row = next((r for r in rows if r["artist_name"].lower() == base.TS_NAME.lower()), None)
        if not ts_row:
            print(f"Taylor Swift not found on the '{args.filter_key}' chart — skipping.")
            sys.exit(0)
    else:
        data = base.load_chart(stats_date, period)
        artists = data["artists"]

        _append_missing_artists(artists)
        genders = load_gender_map() if config.needs_gender else {}
        if config.needs_gender:
            _warn_unclassified(
                [{"rank": base._int_value(a.get("rank")), "artist_id": a.get("artist_id"), "artist_name": a.get("artist_name")} for a in artists if base._int_value(a.get("rank")) is not None],
                genders,
            )

        rows = build_filtered_rows(artists, config.predicate, genders)
        ts_row = next((r for r in rows if r["artist_name"].lower() == base.TS_NAME.lower()), None)
        if not ts_row:
            print(f"Taylor Swift not found in the '{args.filter_key}' subset — skipping.")
            sys.exit(0)

        previous_full = _load_previous_chart(stats_date, period)
        attach_previous_ranks(rows, previous_full, config.predicate, genders)
        ts_row = next(r for r in rows if r["artist_name"].lower() == base.TS_NAME.lower())

    ts_rank = ts_row["rank"]
    print(f"Taylor Swift: subset rank #{ts_rank} / {len(rows)} ({args.filter_key})")

    if ts_rank <= 5:
        mode = "top5"
    elif ts_rank <= 10:
        mode = "top10"
    else:
        mode = "solo"

    skip, detail = _should_skip(mode, ts_row, rows, config.cadence)
    if skip and not args.no_post:
        print(f"[SKIP] Filtered artist post skipped ({args.filter_key}): {detail}.")
        return

    header_img = base.pick_header_image()
    if mode == "top5":
        html = base.build_top5_html(rows, stats_date, header_img, period, title=config.card_title)
        print("Mode: Top 5")
    elif mode == "top10":
        html = base.build_top10_html(rows, stats_date, header_img, period, title=config.card_title)
        print("Mode: Top 10")
    else:
        html = base.build_solo_html(ts_row, stats_date, header_img, period, title=config.card_title, rank_scope=config.rank_scope)
        print(f"Mode: Solo card (Taylor Swift is #{ts_rank} {config.rank_scope})")

    out_path = base.spotify_chart_dir("artists_global", stats_date) / f"artist_chart_{args.filter_key}_image.png"
    base.generate_image(html, out_path)

    if not args.no_post:
        twitter_session = Path(args.session) if args.session else base.TWITTER_SESSION
        if not twitter_session.exists():
            print(f"Twitter session not found: {twitter_session} — skipping post.")
            return
        posted_lock = base.spotify_chart_dir("artists_global", stats_date) / f"artist_chart_{args.filter_key}_{mode}_posted.lock"
        if posted_lock.exists() and not args.force:
            print(f"[SKIP] Filtered artist chart already posted for {stats_date} ({args.filter_key}/{mode})")
            return
        tweet = build_tweet(mode, stats_date, config)
        print(f"\nTweet:\n{tweet}\n")
        from core.twitter import post_with_image
        success = post_with_image(tweet, out_path, twitter_session)
        if success:
            posted_lock.parent.mkdir(parents=True, exist_ok=True)
            posted_lock.touch()
            print("✓ Posté avec succès.")
        else:
            print("✗ Échec du post Twitter.")
            sys.exit(1)
    else:
        print("Twitter post suppressed (--no-post).")


if __name__ == "__main__":
    main()
