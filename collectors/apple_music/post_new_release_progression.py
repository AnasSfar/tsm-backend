#!/usr/bin/env python3
"""post_new_release_progression.py — hourly chart-movement posts for tracks
still inside their release window (default 72h), across Apple Music Global +
key countries and iTunes Store key countries.

Why this exists (2026-09-24, The Encore / 4 new tracks releasing 2026-09-25):
a brand-new track has no previous-day snapshot, so the normal day-over-day
movement markers used everywhere else in Apple Music (`core/csv_utils.py
::load_previous_ranks`, always "vs yesterday" on purpose) can't show anything
useful on release day. This script keeps its OWN small "last seen rank"
state per (track, chart, region) instead, so the delta is always "vs the
last time we checked" — which for a brand-new release is the only baseline
that exists. It naturally goes quiet again once every debut track ages out
of the window (data-rules: never post/estimate a comparison we don't have).

Run once per platform, right after that platform's own collector
(2026-09-24, owner: "whoever is ready first is posted first"):
`--platform itunes` at the end of collectors/itunes/run_itunes.bat (~2 min
cycle) and `--platform apple_music` at the end of run_apple_music.bat
(~15-20 min cycle) — the two chains run in parallel, neither waits for the
other. Each platform has its own state file so the two processes never
overwrite each other's state; the shared X account slot (post_with_image)
serializes the actual posts. Safe to run every cycle even outside a release
window: outside one it only posts the Global card (every day, owner
2026-10-05, see _post_global_daily) — every other post needs a catalog track
whose release_date falls inside APPLE_MUSIC_DEBUT_WINDOW_HOURS.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Tweet text below includes emoji (U+1F3A7 etc.) outside Windows' default
# console codepage (cp1252) -> printing it when stdout/stderr are redirected
# to a file (run_apple_music.bat) raises UnicodeEncodeError and kills the
# whole script. Force UTF-8 on both streams before any such print.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core.discography import iter_catalog_tracks, song_key_candidates, song_name_key  # noqa: E402
from core.card_theme import country_label  # noqa: E402
from ts_page_all import MARKET_WEIGHT_DEFAULT, MARKET_WEIGHTS  # noqa: E402
from collectors.spotify.core.data_paths import (  # noqa: E402
    apple_music_daily_csv,
    itunes_daily_csv,
)
from collectors.comp.discography import (  # noqa: E402
    _norm as _album_norm,
    build_cover_map,
    display_title_for_album,
)

COVERS_PATH = REPO_ROOT / "db" / "discography" / "covers.json"
STATE_DIR = HERE / "tools" / "json"
LOCKS_DIR = HERE / "tools" / "locks" / "new_release_progression"
OUT_DIR = HERE / "tools" / "cards" / "new_release_progression"

# Owner 2026-09-27: "fait le genre 7 jours, on finit a la fin du prochain
# vendredi" (72 h until then) — window_end() rounds up to midnight Paris.
DEBUT_WINDOW_HOURS = float(os.getenv("APPLE_MUSIC_DEBUT_WINDOW_HOURS", "168"))
KEY_COUNTRIES = [
    c.strip().lower()
    for c in os.getenv("APPLE_MUSIC_DEBUT_KEY_COUNTRIES", "us,gb,fr,ca,au").split(",")
    if c.strip()
]
# Stores HIGHLIGHTED on the cards (owner 2026-09-26: IFPI top 10 recorded-music
# markets, in IFPI order). Display only — posting triggers stay on KEY_COUNTRIES.
HIGHLIGHT_COUNTRIES = [
    c.strip().lower()
    for c in os.getenv("APPLE_MUSIC_HIGHLIGHT_COUNTRIES", "us,jp,gb,cn,de,fr,kr,br,ca,mx").split(",")
    if c.strip()
]
TWITTER_SESSION = (
    REPO_ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "twitter_session.json"
)
POST_PRIORITY = int(os.getenv("APPLE_MUSIC_DEBUT_POST_PRIORITY", "1"))
# Runs inside the hourly .bat: waiting the default 30 min for the shared
# @swiftiescharts slot (x4 tracks) would push the cycle past the next trigger.
POST_SLOT_TIMEOUT = int(os.getenv("APPLE_MUSIC_DEBUT_POST_SLOT_TIMEOUT", "180"))
# A card that contains a DEBUT (first post on a chart): priority 0 ("debut
# releases" in the data-rules scale) and a longer wait for the shared account
# slot — giving up after 3 min would push the debut to the next hour
# (2026-09-25, "be the first to post").
DEBUT_POST_PRIORITY = int(os.getenv("APPLE_MUSIC_DEBUT_FIRST_POST_PRIORITY", "0"))
DEBUT_POST_SLOT_TIMEOUT = int(os.getenv("APPLE_MUSIC_DEBUT_FIRST_POST_SLOT_TIMEOUT", "900"))
# A busy slot no longer drops the post to the next hour after 3 min (owner
# 2026-09-26: "mais c'est con") — it waits until HH:<this minute>, when the
# next hourly run is about to take over. The timeouts above stay the floor
# (a post started past that minute still gets them).
# ~2.5 min between this script's posts and the account's previous post
# (owner 2026-09-26) instead of the account-wide 60-75 s. twitter.py reads the
# spacing at import, and it is only imported lazily below — so this applies to
# this process only (Apple Music + iTunes chains, release_watch express posts).
POST_SPACING_SECONDS = int(os.getenv("APPLE_MUSIC_DEBUT_POST_SPACING_SECONDS", "150"))
os.environ["TWITTER_ACCOUNT_SPACING_MIN_SECONDS"] = str(POST_SPACING_SECONDS)
os.environ["TWITTER_ACCOUNT_SPACING_MAX_SECONDS"] = str(POST_SPACING_SECONDS + 15)
POST_SLOT_DEADLINE_MINUTE = int(os.getenv("APPLE_MUSIC_DEBUT_POST_DEADLINE_MINUTE", "50"))
# Caption budget: the account is X Premium, our cap is twitter.py's
# TWITTER_TEXT_LIMIT (500) — never trimmed to fit 280 anymore (the 275 kept
# here until 2026-09-27 dropped sentences / "Also:" songs for nothing).
TEXT_BUDGET = int(os.getenv("TWITTER_TEXT_LIMIT", "500")) - 5


def _slot_timeout(debut: bool) -> int:
    floor = DEBUT_POST_SLOT_TIMEOUT if debut else POST_SLOT_TIMEOUT
    now = datetime.now()
    deadline = now.replace(minute=POST_SLOT_DEADLINE_MINUTE, second=0, microsecond=0)
    return max(floor, int((deadline - now).total_seconds()))
FRONTEND_PUBLIC = REPO_ROOT.parent / "tsm-frontend" / "frontend" / "public"

# Volume control (2026-09-24): a debut, a new #1 or a new peak (any rank) always
# posts; any other move posts at most once every MIN_GAP_HOURS per
# (track, platform), and a cycle where the song only dropped never posts
# unless APPLE_MUSIC_DEBUT_POST_DROPS=1.
MIN_GAP_HOURS = float(os.getenv("APPLE_MUSIC_DEBUT_MIN_GAP_HOURS", "3"))
PEAK_MIN_GAP_MINUTES = float(os.getenv("APPLE_MUSIC_DEBUT_PEAK_GAP_MINUTES", "15"))
POST_DROPS = os.getenv("APPLE_MUSIC_DEBUT_POST_DROPS", "0") == "1"
# Hourly runs post every key-market move (threads since 2026-09-25); 0 = back
# to post_due's throttle (debut/#1/peak now, climbs every 3h, never drops).
POST_EVERY_MOVE = os.getenv("APPLE_MUSIC_DEBUT_POST_EVERY_MOVE", "1") == "1"

# Phone alerts (same topic as the Global Apple Music notification).
NTFY_TOPIC = os.getenv("NTFY_TOPIC_APPLE_MUSIC", "taylormuseum-apple-music")
# Max wait for the per-platform run lock (the express post of the release
# watcher can hold it ~6 min: 5 posts spaced 60-75 s).
POST_ATTEMPTS = int(os.getenv("APPLE_MUSIC_DEBUT_POST_ATTEMPTS", "3"))
POST_RETRY_WAIT = int(os.getenv("APPLE_MUSIC_DEBUT_POST_RETRY_WAIT", "30"))
PLATFORM_LOCK_WAIT = int(os.getenv("APPLE_MUSIC_DEBUT_LOCK_WAIT", "1200"))

# Apple ids of the new tracks (iTunes lookup of album 6814997249, The Encore,
# 2026-09-24). Rows match by id OR title: the Apple Music and iTunes ids of one
# song can differ (The Fate of Ophelia = 1833328840 on Apple Music, 6814997402
# on iTunes) and clean/explicit editions have their own ids. The ids also drive
# the automatic release detection (_apple_availability).
KNOWN_APPLE_IDS: dict[str, set[str]] = {
    "Patient Zero": {"6814997425"},
    "Cleveland!": {"6814997427"},
    "Pink Clouding": {"6814997428"},
    "Babylon": {"6814997429"},
}
# A catalog release_date only makes a track a CANDIDATE: the 72h window starts
# when the song is actually out — first cycle where Apple reports it streamable
# or purchasable, or where it shows up on a chart (owner 2026-09-24: "the debut
# is the first time they appear at the charts or are able to be bought"). If
# Apple can't be reached at all, fall back to release_date + this delay.
CANDIDATE_LOOKAHEAD_HOURS = 48
AVAILABILITY_FALLBACK_HOURS = float(os.getenv("APPLE_MUSIC_DEBUT_FALLBACK_HOURS", "6"))
# Released for this long but on none of this platform's key charts -> alert once
# (title/id mismatch or a broken feed). iTunes only: Apple Music charts can
# legitimately lag a day.
NOT_FOUND_ALERT_HOURS = 2.0

# Short market names for tweets ("on iTunes in the US").
# The new edition on the iTunes Top Albums chart (owner 2026-09-25: an album
# card on iTunes too, same rules as the songs). Explicit + clean edition ids
# seen in the iTunes feeds on 2026-09-24; keyed by the DISPLAY album title.
KNOWN_ALBUM_IDS: dict[str, set[str]] = {
    "The Life of a Showgirl: The Encore": {"6814997249", "6814995859"},
}

MARKET_NAMES = {"us": "the US", "gb": "the UK", "fr": "France", "ca": "Canada", "au": "Australia"}

# Apple Music (streaming) and iTunes (purchases) are separate charts with
# separate site pages — decision 2026-09-24: one card + one post per
# PLATFORM per track per cycle, never both mixed in the same table.
PLATFORMS = {
    # No per-platform color: the card accent comes from the cover (owner
    # 2026-09-24, see _cover_accent).
    "apple_music": {"label": "Apple Music", "site_source": "applemusic",
                    "footer": "Apple Music Charts"},
    "itunes": {"label": "iTunes", "site_source": "itunes",
               "footer": "iTunes Store Charts"},
}


def _album_cover_url(album: str, fallback: str) -> str:
    """covers.json album cover first (holds the current edition's art, e.g.
    The Encore cover for "The Life of a Showgirl"), per-track image_url as
    fallback — per-track URLs in the catalog still point at the original
    standard-edition artwork."""
    try:
        cover = build_cover_map(COVERS_PATH).get(_album_norm(album), "")
    except Exception:
        cover = ""
    return cover if str(cover).startswith("http") else fallback


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scraped-at", dest="scraped_at", default=None)
    parser.add_argument(
        "--platform", choices=["all", *PLATFORMS], default="all",
        help="Only this platform's charts/posts (scheduled runs pass it: each "
             "platform posts right after its own collector, independently).",
    )
    parser.add_argument("--window-hours", type=float, default=DEBUT_WINDOW_HOURS)
    parser.add_argument("--no-post", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Print what would post, write nothing.")
    return parser.parse_args()


def _now_paris() -> datetime:
    try:
        return datetime.now(ZoneInfo("Europe/Paris"))
    except Exception:
        return datetime.now(timezone.utc)


def _parse_release_date(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def candidate_tracks(now: datetime, window_hours: float) -> list[dict]:
    """Catalog tracks whose release_date is close enough to `now` that they may
    be (about to be) out: from CANDIDATE_LOOKAHEAD_HOURS before release_date to
    window + lookahead after it. Being a candidate posts nothing — see
    resolve_released_at()."""
    candidates: list[dict] = []
    seen_keys: set[str] = set()
    for track in iter_catalog_tracks():
        catalog_release = _parse_release_date(track.get("release_date"))
        if catalog_release is None:
            continue
        hours_since = (now - catalog_release).total_seconds() / 3600.0
        if hours_since < -CANDIDATE_LOOKAHEAD_HOURS or hours_since > window_hours + CANDIDATE_LOOKAHEAD_HOURS:
            continue
        title = str(track.get("title") or track.get("base_title") or "").strip()
        if not title:
            continue
        key = song_name_key(track.get("base_title") or title)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        candidates.append(
            {
                "title": title,
                "key": key,
                "keys": set(song_key_candidates(title)),
                "apple_ids": set(KNOWN_APPLE_IDS.get(title, set())),
                "image_url": _album_cover_url(str(track.get("album") or ""), str(track.get("image_url") or "")),
                "album": str(track.get("album") or ""),
                "catalog_release": catalog_release,
            }
        )
    candidates += feed_release_tracks(now, window_hours, seen_keys)
    return candidates


def feed_release_tracks(now: datetime, window_hours: float, taken: set[str]) -> list[dict]:
    """New Taylor tracks that are NOT in db/discography/, found in the iTunes
    Top Songs CSVs with a recent Apple `release_date` (owner 2026-10-10:
    "Patient Zero (Acoustic Version)" / "(Piano Version)" singles hit #1/#2 in
    the US and nothing posted — the catalog had no row for them). A lighter
    treatment than a catalog release, on purpose ("on va pas bouger tous les
    collectors comme le run début"): nothing outside this script sees them,
    and album = "" so no album thread / album card — only the song posts
    (key-market #1, good updates). The parent album is kept in "emoji_album"
    for the caption emoji only."""
    catalog_keys: set[str] = set()
    album_of: dict[str, str] = {}
    for track in iter_catalog_tracks():
        for value in (track.get("title"), track.get("base_title")):
            for key in song_key_candidates(value):
                catalog_keys.add(key)
                album_of.setdefault(key, str(track.get("album") or ""))
    found: dict[str, dict] = {}
    days = int((window_hours + CANDIDATE_LOOKAHEAD_HOURS) // 24) + 1
    for back in range(days + 1):
        day = (now - timedelta(days=back)).strftime("%Y-%m-%d")
        for row in _read_today_rows(itunes_daily_csv(day, "itunes_top_songs.csv")):
            title = str(row.get("song_name") or "").strip()
            if not title or "taylor swift" not in str(row.get("artist_name") or "").lower():
                continue
            key = song_name_key(title)
            if key in taken or catalog_keys.intersection(song_key_candidates(title)):
                continue
            entry = found.setdefault(key, {"title": title, "key": key, "keys": set(song_key_candidates(title)),
                                           "apple_ids": set(), "image_url": "", "album": "",
                                           "subtitle": "Taylor Swift · Single", "catalog_release": None})
            apple_id = str(row.get("apple_music_id") or "").strip()
            if apple_id:
                entry["apple_ids"].add(apple_id)
            entry["image_url"] = entry["image_url"] or str(row.get("image_url") or "")
            released = _parse_release_date(row.get("release_date"))
            if released is not None and (entry["catalog_release"] is None or released < entry["catalog_release"]):
                entry["catalog_release"] = released
    out: list[dict] = []
    for key, entry in found.items():
        released = entry["catalog_release"]
        if released is None:
            continue  # no Apple release date at all: never NEW by inference
        hours_since = (now - released).total_seconds() / 3600.0
        if hours_since < -CANDIDATE_LOOKAHEAD_HOURS or hours_since > window_hours + CANDIDATE_LOOKAHEAD_HOURS:
            continue
        base = re.sub(r"\s*\([^()]*\)\s*$", "", key).strip()
        entry["emoji_album"] = album_of.get(base, "")
        taken.add(key)
        out.append(entry)
    return out


def _apple_availability(ids: set[str], countries: list[str]) -> dict[str, bool] | None:
    """{apple_id: available} from the public iTunes lookup API — available =
    streamable OR purchasable in at least one of `countries` (before release
    Apple lists the tracks of a pre-order with isStreamable=false and no
    trackPrice; Apple's own releaseDate on these rows is not reliable). None
    when no country could be checked at all (network down)."""
    import urllib.request

    if not ids:
        return None
    result = {i: False for i in ids}
    checked = False
    for country in countries:
        url = f"https://itunes.apple.com/lookup?id={','.join(sorted(ids))}&country={country}"
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            print(f"[new_release_progression] WARN: availability lookup failed ({country}): {exc}")
            continue
        checked = True
        for item in payload.get("results") or []:
            track_id = str(item.get("trackId") or "")
            if track_id in result and (item.get("isStreamable") or item.get("trackPrice") is not None):
                result[track_id] = True
    return result if checked else None


def _seen_on_charts(track: dict, sources: list[dict], cache: dict[Path, list[dict]]) -> bool:
    for source in sources:
        rows = _source_latest_rows(source, cache)[1]
        if source["country"] is not None:
            rows = [r for r in rows if str(r.get("country") or "").lower() == source["country"]]
        if any(_row_matches(track, r) for r in rows):
            return True
    return False


def resolve_released_at(
    candidates: list[dict], now: datetime, state: dict, sources: list[dict], cache: dict[Path, list[dict]],
) -> list[str]:
    """Sets track["released_at"] (datetime or None) on every candidate.
    Detected once, then remembered in state (`__released_at|<key>`). Returns
    the titles detected THIS run (for the phone alert)."""
    # A release is one event for both chains (2026-09-25: Apple's lookup API
    # still said "not streamable" 5 h after the drop and the Encore songs were
    # on no Apple Music chart yet, so the Apple Music chain never saw it — and
    # without a track in window it skipped the Global card too). Whatever one
    # chain detected, the other adopts.
    others = [load_state(p) for p in PLATFORMS if state_path(p).exists()]
    for track in candidates:
        k = f"__released_at|{track['key']}"
        if not state.get(k):
            seen = sorted(o[k] for o in others if o.get(k))
            if seen:
                state[k] = seen[0]
                print(f"[new_release_progression] RELEASE KNOWN: {track['title']} (detected by the other chain at {seen[0]})")
    pending = [t for t in candidates if not state.get(f"__released_at|{t['key']}")]
    availability = None
    ids = set().union(*(t["apple_ids"] for t in pending)) if pending else set()
    if ids:
        availability = _apple_availability(ids, KEY_COUNTRIES)
    detected_now: list[str] = []
    for track in candidates:
        stored = _parse_release_date(state.get(f"__released_at|{track['key']}") or "")
        if stored is None:
            reason = ""
            if availability and any(availability.get(i) for i in track["apple_ids"]):
                reason = "available on Apple"
            elif _seen_on_charts(track, sources, cache):
                reason = "seen on a chart"
            elif (availability is None or not track["apple_ids"]) and (
                now - track["catalog_release"]
            ).total_seconds() / 3600.0 >= AVAILABILITY_FALLBACK_HOURS:
                reason = "catalog release_date (Apple unreachable / no known id)"
            if reason:
                stored = now
                state[f"__released_at|{track['key']}"] = now.isoformat()
                detected_now.append(track["title"])
                print(f"[new_release_progression] RELEASE DETECTED: {track['title']} ({reason})")
        track["released_at"] = stored
    return detected_now


def window_end(released: datetime, window_hours: float) -> datetime:
    """window_hours after the detected release, then to the end of that day
    (Paris): a Friday release (Encore, out Fri 06:35) ends the next Friday at
    midnight."""
    end = (released + timedelta(hours=window_hours)).astimezone(ZoneInfo("Europe/Paris"))
    return (end + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def active_debut_tracks(candidates: list[dict], now: datetime, window_hours: float) -> list[dict]:
    active = []
    for track in candidates:
        released = track.get("released_at")
        if released is None:
            continue
        if now < window_end(released, window_hours):
            track["release_date"] = released  # peak history starts here
            active.append(track)
    return active


def _read_today_rows(csv_path: Path) -> list[dict]:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _latest_cycle_rows(rows: list[dict]) -> tuple[str, list[dict]]:
    """Rows from the most recent scraped_at present in `rows` (this cycle,
    whatever its exact timestamp — Apple Music and iTunes round independently
    so their scraped_at values don't always match to the second)."""
    if not rows:
        return "", []
    latest = max(str(r.get("scraped_at") or "") for r in rows)
    return latest, [r for r in rows if str(r.get("scraped_at") or "") == latest]


def _row_lookup(rows: list[dict], country: str | None = None) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for row in rows:
        if country is not None and str(row.get("country") or "").lower() != country:
            continue
        for key in song_key_candidates(row.get("song_name")):
            lookup.setdefault(key, row)
    return lookup


def _row_matches(track: dict, row: dict) -> bool:
    """Known Apple id first, title as fallback (clean/explicit editions and the
    Apple Music vs iTunes ids of one song differ)."""
    if track.get("apple_ids") and str(row.get("apple_music_id") or "").strip() in track["apple_ids"]:
        return True
    keys = track.get("keys") or set(song_key_candidates(track["title"]))
    row_keys = row.get("_keys")
    if row_keys is None:
        row_keys = set(song_key_candidates(row.get("song_name") or row.get("album_name")))
    return bool(keys.intersection(row_keys))


def _index(path: Path, cache: dict) -> tuple[list[dict], dict[str, list[dict]]]:
    """A daily CSV read once per run: rows (each with its match keys "_keys"
    precomputed) + rows grouped by country. The all-regions cards (2026-09-25)
    look up ~170 countries x several days — no per-country rescans."""
    key = ("index", path)
    if key not in cache:
        if path not in cache:
            cache[path] = _read_today_rows(path)
        by_country: dict[str, list[dict]] = {}
        for row in cache[path]:
            if "_keys" not in row:
                row["_keys"] = set(song_key_candidates(row.get("song_name") or row.get("album_name")))
            by_country.setdefault(str(row.get("country") or "").lower(), []).append(row)
        cache[key] = by_country
    return cache[path], cache[key]


def _rank_of(row: dict) -> int | None:
    try:
        return int(str(row.get("rank") or "").strip())
    except ValueError:
        return None


def _source_latest_rows(source: dict, cache: dict) -> tuple[str, list[dict]]:
    """(latest scraped_at of the WHOLE file, this source's rows at it). The
    latest cycle is file-wide on purpose: a country missing from the latest
    cycle must not fall back to an older rank."""
    path, filt, country = source["path"], source.get("row_filter"), source.get("country")
    rows, by_country = _index(path, cache)
    lkey = ("latest", path, source.get("filter_name") or bool(filt))
    if lkey not in cache:
        filtered = [r for r in rows if not filt or filt(r)]
        cache[lkey] = max((str(r.get("scraped_at") or "") for r in filtered), default="")
    latest = cache[lkey]
    if not latest:
        return "", []
    pool = by_country.get(country, []) if country is not None else rows
    return latest, [r for r in pool if str(r.get("scraped_at") or "") == latest and (not filt or filt(r))]


def _best_match(track: dict, rows: list[dict]) -> tuple[dict | None, int | None]:
    """Best-ranked matching row (iTunes lists explicit + clean editions)."""
    best_row, best_rank = None, None
    for row in rows:
        if not _row_matches(track, row):
            continue
        rank = _rank_of(row)
        if rank is not None and (best_rank is None or rank < best_rank):
            best_row, best_rank = row, rank
    return best_row, best_rank


def _day_countries(path: Path) -> list[str]:
    """Key markets first, then every other country present in the day's CSV."""
    seen = {str(r.get("country") or "").lower() for r in _read_today_rows(path)} - {""}
    return KEY_COUNTRIES + sorted(seen - set(KEY_COUNTRIES))


def build_sources(today: str, express: dict[str, Path] | None = None) -> list[dict]:
    """Every region of the day (owner 2026-09-25: the card lists ALL the
    regions a song charts in, like the site's share image). Only the key
    markets (+ Global) can TRIGGER a post ("key": True) — otherwise ~170
    countries moving every hour would post every hour."""
    am_countries = _day_countries(apple_music_daily_csv(today, "apple_music_country_charts.csv"))
    # Express (release_watch): countries of the express files — a new song can
    # chart where no Taylor song did in today's snapshot.
    it_songs = _day_countries(express["song"] if express else itunes_daily_csv(today, "itunes_top_songs.csv"))
    it_albums = _day_countries(express["album"] if express else itunes_daily_csv(today, "itunes_top_albums.csv"))
    sources: list[dict] = [
        {
            "source_key": "am_global",
            "platform": "apple_music",
            "region": "global",
            "chart_label": "Global",
            "path": apple_music_daily_csv(today, "apple_music_global.csv"),
            "path_for": lambda d: apple_music_daily_csv(d, "apple_music_global.csv"),
            "row_filter": lambda r: str(r.get("chart_type") or "") == "global",
            "filter_name": "global",
            "country": None,
            "key": True,
        }
    ]
    for country in am_countries:
        sources.append(
            {
                "source_key": f"am_{country}",
                "platform": "apple_music",
                "region": country,
                "chart_label": country_label(country),
                "path": apple_music_daily_csv(today, "apple_music_country_charts.csv"),
                "path_for": lambda d: apple_music_daily_csv(d, "apple_music_country_charts.csv"),
                "row_filter": None,
                "country": country,
                "key": country in KEY_COUNTRIES,
            }
        )
    # Apple Music Top Albums per country: the album card, like the site's
    # album share image (owner 2026-09-25, same card as the iTunes albums one).
    for country in _day_countries(apple_music_daily_csv(today, "apple_music_country_albums.csv")):
        sources.append(
            {
                "source_key": f"am_albums_{country}",
                "platform": "apple_music",
                "kind": "album",  # only matched against the album item
                "region": country,
                "chart_label": country_label(country),
                "path": apple_music_daily_csv(today, "apple_music_country_albums.csv"),
                "path_for": lambda d: apple_music_daily_csv(d, "apple_music_country_albums.csv"),
                "row_filter": None,
                "country": country,
                "key": country in KEY_COUNTRIES,
            }
        )
    for country in it_songs:
        sources.append(
            {
                "source_key": f"itunes_{country}",
                "platform": "itunes",
                "region": country,
                "chart_label": country_label(country),
                "path": itunes_daily_csv(today, "itunes_top_songs.csv"),
                "path_for": lambda d: itunes_daily_csv(d, "itunes_top_songs.csv"),
                "row_filter": None,
                "country": country,
                "key": country in KEY_COUNTRIES,
            }
        )
    for country in it_albums:
        sources.append(
            {
                "source_key": f"itunes_albums_{country}",
                "platform": "itunes",
                "kind": "album",  # only matched against the album item
                "region": country,
                "chart_label": country_label(country),
                "path": itunes_daily_csv(today, "itunes_top_albums.csv"),
                "path_for": lambda d: itunes_daily_csv(d, "itunes_top_albums.csv"),
                "row_filter": None,
                "country": country,
                "key": country in KEY_COUNTRIES,
            }
        )
    return sources


# 2026-09-26 (owner, ~24h after the Encore): fewer posts, in two steps.
# Morning: the songs thread became a thread of every song of the album
# (ALBUM_SONGS_THREAD, now off). Noon ("les thread oublie les"):
#   - no more songs threads (SONG_THREADS); a song card posts on its own ONLY
#     when the song reaches #1 on a key chart (SONG_POSTS_NUMBER_ONE_ONLY);
#   - Apple Music: the album-filtered card of the US, US Pop, Australia and
#     Canada charts (REGION_CARD_COUNTRIES / POP_CARD_COUNTRIES) — the Global
#     and Global album cards stop (GLOBAL_CARDS);
#   - iTunes: the album card of the 5 key countries as ONE thread
#     (_post_itunes_album_thread);
#   - Top Albums cards (both platforms): off (ALBUM_CHART_CARDS).
ALBUM_SONGS_THREAD = os.getenv("APPLE_MUSIC_DEBUT_ALBUM_SONGS_THREAD", "0") == "1"
ALBUM_CHART_CARDS = os.getenv("APPLE_MUSIC_DEBUT_ALBUM_CHART_CARDS", "0") == "1"
SONG_THREADS = os.getenv("APPLE_MUSIC_DEBUT_SONG_THREADS", "0") == "1"
SONG_POSTS_NUMBER_ONE_ONLY = os.getenv("APPLE_MUSIC_DEBUT_SONG_POSTS_NUMBER_ONE_ONLY", "1") == "1"
# 2026-09-27 (owner: "on peut poster une chanson en standalone si elle reçoit
# une bonne update genre plusieurs RE PEAK ou NEW PEAK, mais pas en abusant,
# juste les meilleurs"): besides the #1 rule, a song card also posts when THIS
# cycle brings it several peaks inside the top UPDATE_TOP, weighted by market
# (AM_MARKET_WEIGHTS, Global 1.5): new peak = 1, back at its peak = 0.5, +1
# when that peak is a new #1. Needs >= UPDATE_MIN_PEAKS peaks and a score >=
# UPDATE_MIN_SCORE; the best song only, per platform and cycle; a song not
# again within UPDATE_GAP_HOURS on that platform. Calibrated on 26-27/09:
# 4 posts in 36h (Babylon US #3 / UK #3, Cleveland! MX #2 & PT #2...).
SONG_UPDATE_POSTS = os.getenv("APPLE_MUSIC_DEBUT_SONG_UPDATE_POSTS", "1") == "1"
UPDATE_TOP = int(os.getenv("APPLE_MUSIC_DEBUT_UPDATE_TOP", "10"))
UPDATE_MIN_PEAKS = int(os.getenv("APPLE_MUSIC_DEBUT_UPDATE_MIN_PEAKS", "2"))
UPDATE_MIN_SCORE = float(os.getenv("APPLE_MUSIC_DEBUT_UPDATE_MIN_SCORE", "0.8"))
UPDATE_GAP_HOURS = float(os.getenv("APPLE_MUSIC_DEBUT_UPDATE_GAP_HOURS", "12"))
UPDATE_GLOBAL_WEIGHT = 1.5
GLOBAL_CARDS = os.getenv("APPLE_MUSIC_DEBUT_GLOBAL_CARDS", "0") == "1"
# Afternoon (owner: "garde les finalement mais genre dans un thread"): the
# Global + Global album cards come back, as one thread (_post_global_thread).
# 2026-09-27: only the Global card, one post at every Global update
# (GLOBAL_ALBUM_CARD=1 puts the album card back as the thread's reply).
# 2026-10-05 (owner: "on doit poster global apple music charts chaque jour pas
# seulement les debuts"): the Global card no longer needs a release window —
# posted at every Global update (Apple refreshes it once a day, ~10:00 Paris).
GLOBAL_THREAD = os.getenv("APPLE_MUSIC_DEBUT_GLOBAL_THREAD", "1") == "1"
GLOBAL_ALBUM_CARD = os.getenv("APPLE_MUSIC_DEBUT_GLOBAL_ALBUM_CARD", "0") == "1"


def album_song_tracks(active_tracks: list[dict]) -> list[dict]:
    """The other songs of the new tracks' album(s), from the catalog: main
    versions only (no acoustic / remix / Track by Track, i.e. no extra_type).
    "old": their release predates our history (Apple Music 2026-03-22), so
    their cards/tweets never claim NEW / NEW PEAK / RE-PEAK and show no peak."""
    albums = {t["album"] for t in active_tracks if t["album"]}
    taken = {t["key"] for t in active_tracks}
    released = min((t["released_at"] for t in active_tracks), default=None)
    out: list[dict] = []
    for track in iter_catalog_tracks():
        album = str(track.get("album") or "")
        if album not in albums or track.get("extra_type"):
            continue
        title = str(track.get("title") or track.get("base_title") or "").strip()
        key = song_name_key(track.get("base_title") or title)
        if not title or key in taken:
            continue
        taken.add(key)
        out.append({
            "title": title,
            "key": key,
            "keys": set(song_key_candidates(title)),
            "apple_ids": set(KNOWN_APPLE_IDS.get(title, set())),
            "image_url": _album_cover_url(album, str(track.get("image_url") or "")),
            "album": album,
            "released_at": released,
            "release_date": None,
            "old": True,
        })
    return out


def album_items(active_tracks: list[dict]) -> list[dict]:
    """One pseudo-track per album of the active tracks, for its iTunes Top
    Albums card. Out when its first new track is out."""
    items = []
    for album in sorted({t["album"] for t in active_tracks if t["album"]}):
        display = display_title_for_album(album)
        released = min(t["released_at"] for t in active_tracks if t["album"] == album)
        items.append(
            {
                "title": display,
                "key": f"album|{song_name_key(display)}",
                "keys": set(song_key_candidates(display)),
                "apple_ids": set(KNOWN_ALBUM_IDS.get(display, set())),
                "image_url": _album_cover_url(album, ""),
                "album": album,
                "subtitle": "Taylor Swift · Album",
                "released_at": released,
                "release_date": released,
                "is_album": True,
            }
        )
    return items


def state_path(platform: str) -> Path:
    """One state file per platform: the iTunes and Apple Music runs are
    separate processes that can overlap, a shared file would lose updates."""
    return STATE_DIR / f"new_release_progression_state_{platform}.json"


def load_state(platform: str) -> dict:
    path = state_path(platform)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict, platform: str) -> None:
    path = state_path(platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _safe_slug(*parts: str) -> str:
    import re

    text = "_".join(parts)
    text = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return text or "track"


def _file_data_uri(path: Path, mime: str) -> str:
    import base64

    try:
        return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception:
        return ""


def _remote_data_uri(url: str) -> str:
    try:
        from collectors.comp.img_fetch import fetch_data_uri

        return fetch_data_uri(url) or ""
    except Exception:
        return ""


_GLOBE_SVG = (
    '<svg class="region-flag region-flag-globe" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.8" aria-hidden="true"><circle cx="12" cy="12" r="9" />'
    '<path d="M3 12h18M12 3c2.5 2.5 3.8 5.7 3.8 9S14.5 18.5 12 21c-2.5-2.5-3.8-5.7-3.8-9S9.5 5.5 12 3z" /></svg>'
)


def _cover_accent(img_data_uri: str) -> str:
    """Accent sampled from the song/album cover — same readable-on-light
    extraction as the Spotify Charts cards (comp.chart_card._cover_palette:
    dominant saturated cover color, clamped so text stays legible on a light
    card). Falls back to its own default if the cover can't be decoded."""
    import base64

    try:
        from collectors.comp.chart_card import _cover_palette

        raw = base64.b64decode(img_data_uri.split(",", 1)[1]) if img_data_uri.startswith("data:") else b""
        return _cover_palette(raw)["accent"]
    except Exception:
        return "#f05e33"


def _region_flag_html(region: str) -> str:
    """Same as the site's components/RegionFlag.jsx: globe glyph for Global,
    flagcdn SVG otherwise (uk -> gb)."""
    key = (region or "").strip().lower()
    if key in {"global", "worldwide", "world", "ww"}:
        return _GLOBE_SVG
    iso = {"uk": "gb", "il": "ps"}.get(key, key)
    if len(iso) != 2:
        return ""
    uri = _flag_data_uri(iso)
    return f'<img class="region-flag" src="{uri}" alt="" />' if uri else ""


_FLAG_CACHE: dict[str, str] = {}
FLAG_CACHE_DIR = HERE / "tools" / "cache" / "flags"


def _flag_data_uri(iso: str) -> str:
    """flagcdn SVG as a data URI, cached in memory and on disk (flags never
    change; an all-regions card has up to ~170)."""
    if iso in _FLAG_CACHE:
        return _FLAG_CACHE[iso]
    path = FLAG_CACHE_DIR / f"{iso}.datauri"
    uri = ""
    try:
        uri = path.read_text(encoding="ascii") if path.exists() else ""
    except OSError:
        uri = ""
    if not uri:
        uri = _remote_data_uri(f"https://flagcdn.com/{iso}.svg")
        if uri:
            try:
                FLAG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                path.write_text(uri, encoding="ascii")
            except OSError:
                pass
    _FLAG_CACHE[iso] = uri
    return uri


def _format_clock(value: str) -> str:
    """Site's formatCollectorClock in "en" (e.g. "2:00 AM CEST"), rendered in
    Europe/Paris since a posted PNG has no visitor timezone."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("Europe/Paris"))
        dt = dt.astimezone(ZoneInfo("Europe/Paris"))
    except Exception:
        return ""
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'} {dt.tzname() or ''}".strip()


def _format_share_date(value: str) -> str:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return str(value or "")[:10]
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def _html_escape(value: object) -> str:
    import html as html_lib

    return html_lib.escape(str(value or ""))


# ---------------------------------------------------------------------------
# Rank-groups card (owner 2026-09-26). Replaced the site-style "all regions"
# tables (118 rows over 3 columns were unreadable once posted on X): big tiles
# for Global + the highlighted stores, then countries grouped by rank
# ("#1 · 40 countries", #2, #3, #4–10, #11–50, #51+) as flag + name chips
# sized to their text. Shared by the live posts (_build_progression_card_html,
# with ▲/▼ vs the previous cycle) and daily_recap.py (best rank of the day).
# ---------------------------------------------------------------------------

# No iTunes music store there: their feed is always empty ("cn/kr: 0 song(s)"
# every run) -> no tile, a "—" would read as "not charting".
ITUNES_NO_MUSIC_STORE = {"cn", "kr"}
RANK_GROUPS = [(1, 1, "#1"), (2, 2, "#2"), (3, 3, "#3"), (4, 10, "#4–10"), (11, 50, "#11–50"), (51, 10**6, "#51+")]


def _brand_platform(platform: str) -> str:
    return "apple_music" if platform.startswith("apple_music") else platform  # + "apple_music_albums"


def rank_card_page(*, image_url: str, title: str, subtitle: str, platform: str, chart_label: str,
                   when_main: str, when_sub: str, body_html: str, footer_left: str, tile_count: int = 0) -> str:
    """Shell of the rank cards: header (cover, title, platform brand, date),
    body, footer (@swiftiescharts + logo). Accent = the cover's color."""
    esc = _html_escape
    img_uri = _remote_data_uri(image_url or "") or (image_url or "")
    accent = _cover_accent(img_uri)
    logo_mask = _file_data_uri(FRONTEND_PUBLIC / "logo.png", "image/png")
    brand_platform = _brand_platform(platform)
    if brand_platform == "apple_music":
        brand = _file_data_uri(FRONTEND_PUBLIC / "icons" / "apple-music-logo.webp", "image/webp")
    else:
        brand = _file_data_uri(HERE / "itunes_logo.svg", "image/svg+xml")
    label = PLATFORMS[brand_platform]["label"] + (f" · {chart_label}" if chart_label else "")
    tile_cols = tile_count if tile_count <= 6 else -(-tile_count // 2)
    cover = f'<img class="cover" src="{esc(img_uri)}" alt="" />' if img_uri else ""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/><style>
  :root {{ --text:#101828; --muted:#667085; --line:rgba(16,24,40,.09); --accent:{accent}; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:#f9fcfa; font-family:Inter, system-ui, "Segoe UI", sans-serif; color:var(--text); }}
  #card {{ width:1400px; background:#f9fcfa; padding:28px 32px 20px; }}
  .head {{ display:flex; align-items:center; gap:18px; padding-bottom:18px; border-bottom:1px solid var(--line); }}
  .cover {{ width:84px; height:84px; border-radius:14px; object-fit:cover; box-shadow:0 2px 10px rgba(0,0,0,.15); }}
  .title {{ font-size:34px; font-weight:800; letter-spacing:-.02em; line-height:1.1; }}
  .album {{ font-size:18px; color:var(--muted); margin-top:4px; }}
  .side {{ margin-left:auto; text-align:right; }}
  .brand {{ display:flex; align-items:center; justify-content:flex-end; gap:8px; font-size:15px; font-weight:800;
           text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }}
  .brand img {{ width:24px; height:24px; border-radius:5px; }}
  .when {{ font-size:18px; font-weight:800; margin-top:6px; }}
  .when-sub {{ font-size:14px; font-weight:600; color:var(--muted); margin-top:2px; }}
  .tiles {{ display:grid; grid-template-columns:repeat({max(tile_cols, 1)}, 1fr); gap:12px; margin:20px 0 6px; }}
  .tile {{ background:#fff; border:1px solid var(--line); border-radius:14px; padding:12px 14px; }}
  .tile-one {{ background:color-mix(in srgb, var(--accent) 14%, #fff); border-color:color-mix(in srgb, var(--accent) 40%, #fff); }}
  .tile-off {{ opacity:.55; }}
  .tile-name {{ display:flex; align-items:center; gap:8px; font-size:15px; font-weight:700; color:var(--muted); white-space:nowrap; }}
  .tile-rank {{ display:flex; align-items:baseline; gap:8px; font-size:40px; font-weight:900; letter-spacing:-.03em;
               margin-top:4px; font-variant-numeric:tabular-nums; }}
  .tile-rank .delta {{ font-size:17px; letter-spacing:0; }}
  .tile-one .tile-rank {{ color:var(--accent); }}
  .group {{ margin-top:18px; }}
  .group h3 {{ display:flex; align-items:baseline; gap:12px; margin-bottom:10px; }}
  .g-rank {{ font-size:28px; font-weight:900; letter-spacing:-.02em; }}
  .group-one .g-rank {{ color:var(--accent); }}
  .g-count {{ font-size:18px; font-weight:700; color:var(--muted); }}
  /* Chips sized to their text and wrapped (owner 2026-09-26) — a name is never cut. */
  .chips {{ display:flex; flex-wrap:wrap; gap:8px; }}
  .chip {{ display:flex; align-items:center; gap:9px; background:#fff; border:1px solid var(--line);
          border-radius:10px; padding:8px 12px; font-size:17px; font-weight:600; white-space:nowrap; }}
  .chip-key {{ border-color:var(--accent); box-shadow:inset 3px 0 0 var(--accent); font-weight:800; }}
  .chip-name {{ line-height:1.15; }}
  .chip-rank {{ font-weight:800; font-variant-numeric:tabular-nums; margin-left:2px;
               padding-left:9px; border-left:1px solid var(--line); }}
  .delta {{ font-size:14px; font-weight:800; font-variant-numeric:tabular-nums; }}
  .delta.up {{ color:#1f9d55; }}
  .delta.down {{ color:#d64545; }}
  .delta.new {{ color:#fff; background:var(--accent); border-radius:999px; padding:1px 7px; font-size:11px; letter-spacing:.04em; }}
  .star {{ color:var(--accent); font-size:.9em; margin-left:2px; }}
  .region-flag {{ width:26px; height:19px; flex-shrink:0; object-fit:cover; border-radius:3px;
                 border:1px solid rgba(16,24,40,.08); }}
  .region-flag-globe {{ width:20px; height:20px; border:none; color:var(--muted); }}
  .stats {{ display:grid; grid-template-columns:repeat(6, 1fr); gap:10px; margin-top:10px; }}
  .stat {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:10px 12px; }}
  .stat-label {{ font-size:13px; font-weight:800; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }}
  .stat-value {{ font-size:32px; font-weight:900; letter-spacing:-.02em; font-variant-numeric:tabular-nums; margin-top:2px; }}
  .stat-one {{ background:color-mix(in srgb, var(--accent) 14%, #fff); border-color:color-mix(in srgb, var(--accent) 40%, #fff); }}
  .stat-one .stat-value {{ color:var(--accent); }}
  .stat-zero .stat-value {{ color:#c0c7d2; }}
  .item {{ margin-top:20px; }}
  .item-head {{ display:flex; align-items:center; gap:12px; }}
  .item-cover {{ width:48px; height:48px; border-radius:9px; object-fit:cover; }}
  .item-title {{ font-size:24px; font-weight:800; letter-spacing:-.01em; }}
  .item-rank {{ font-size:24px; font-weight:900; color:var(--muted); min-width:36px; }}
  .foot {{ margin-top:22px; padding-top:14px; border-top:1px solid var(--line); display:flex; align-items:center;
          justify-content:space-between; font-size:13px; font-weight:700; letter-spacing:.03em; text-transform:uppercase; color:var(--muted); }}
  .foot-brand {{ display:flex; align-items:center; gap:9px; }}
  .brand-logo {{ width:26px; height:26px; background-color:var(--text);
    -webkit-mask:url("{logo_mask}") center/contain no-repeat; mask:url("{logo_mask}") center/contain no-repeat; }}
</style></head><body><div id="card">
  <div class="head">{cover}<div><div class="title">{esc(title)}</div>
    <div class="album">{esc(subtitle)}</div></div>
    <div class="side"><div class="brand"><img src="{brand}" alt=""/>{esc(label)}</div>
      <div class="when">{esc(when_main)}</div>
      <div class="when-sub">{esc(when_sub)}</div></div></div>
  {body_html}
  <div class="foot"><span>{footer_left}</span>
    <span class="foot-brand">@swiftiescharts <span class="brand-logo"></span> thetsmuseum.app</span></div>
</div></body></html>"""


def _delta_html(p: dict) -> str:
    """Live cards: move vs the previous cycle ("delta" > 0 = up), NEW on a
    first appearance on that chart, nothing when unchanged."""
    if p.get("is_new"):
        return '<span class="delta new">NEW</span>'
    delta = p.get("delta")
    if not delta:
        return ""
    return (f'<span class="delta up">▲{delta}</span>' if delta > 0
            else f'<span class="delta down">▼{abs(delta)}</span>')


def rank_groups_card_html(*, title: str, subtitle: str, image_url: str, platform: str, placements: list[dict],
                          when_main: str, when_sub: str, footer_left: str, chart_label: str = "",
                          show_deltas: bool = False, show_new_peaks: bool = False) -> str:
    """Tiles (Global when the platform has one + HIGHLIGHT_COUNTRIES), then the
    countries grouped by rank. placements: {region, label, rank} + optional
    delta / is_new (live) and new_peak (★)."""
    esc = _html_escape
    by_region = {p["region"]: p for p in placements}
    countries = sorted((p for p in placements if p["region"] != "global"),
                       key=lambda p: (p["rank"], _market_order(p)))
    extras = lambda p: ((_delta_html(p) if show_deltas else "")  # noqa: E731
                        + ('<span class="star">★</span>' if show_new_peaks and p.get("new_peak") else ""))
    tiles = []
    for region in ["global", *HIGHLIGHT_COUNTRIES]:
        if region == "global" and platform != "apple_music":
            continue  # no Global iTunes / Apple Music albums chart
        if platform == "itunes" and region in ITUNES_NO_MUSIC_STORE:
            continue
        p = by_region.get(region)
        name = "Global" if region == "global" else country_label(region)
        rank = f"#{p['rank']}" if p else "—"
        cls = "tile" + (" tile-one" if p and p["rank"] == 1 else "") + ("" if p else " tile-off")
        tiles.append(f'<div class="{cls}"><div class="tile-name">{_region_flag_html(region)}'
                     f'<span>{esc(name)}</span></div><div class="tile-rank">{rank}{extras(p) if p else ""}</div></div>')
    groups = []
    for lo, hi, group_title in RANK_GROUPS:
        members = [p for p in countries if lo <= p["rank"] <= hi]
        if not members:
            continue
        chips = "".join(
            f'<div class="chip{" chip-key" if p["region"] in HIGHLIGHT_COUNTRIES else ""}">'
            f'{_region_flag_html(p["region"])}<span class="chip-name">{esc(p["label"])}</span>'
            + (f'<span class="chip-rank">#{p["rank"]}</span>' if lo != hi else "")
            + extras(p) + "</div>"
            for p in members
        )
        n = len(members)
        groups.append(
            f'<section class="group{" group-one" if lo == 1 else ""}"><h3><span class="g-rank">{group_title}</span>'
            f'<span class="g-count">{n} {"country" if n == 1 else "countries"}</span></h3>'
            f'<div class="chips">{chips}</div></section>'
        )
    body = f'<div class="tiles">{"".join(tiles)}</div>{"".join(groups)}'
    return rank_card_page(image_url=image_url, title=title, subtitle=subtitle, platform=platform,
                          chart_label=chart_label, when_main=when_main, when_sub=when_sub, body_html=body,
                          footer_left=footer_left, tile_count=len(tiles))



def _build_progression_card_html(
    *, track: dict, placements: list[dict], scraped_at: str, platform: str,
) -> str:
    """Live card of a post (owner 2026-09-26: the site-style tables of up to
    ~170 rows were unreadable on X) -> rank_groups_card_html: the cycle's rank
    per country, ▲/▼ vs the previous cycle, NEW on a first appearance on that
    chart, ★ new peak since release."""
    conf = PLATFORMS[platform]
    prev_times = [p["prev_scraped_at"] for p in placements if p.get("prev_scraped_at")]
    compared = _format_clock(max(prev_times)) if prev_times else ""
    return rank_groups_card_html(
        title=track["title"],
        subtitle=track.get("subtitle") or display_title_for_album(track.get("album") or ""),
        image_url=track.get("image_url") or "", platform=platform, placements=placements,
        when_main=f"{_format_share_date(scraped_at)} · {_format_clock(scraped_at)}",
        when_sub=f"▲▼ vs {compared}" if compared else "rank on each chart right now",
        footer_left=f'{_html_escape(conf["footer"])} · {_html_escape(_format_share_date(scraped_at))}'
                    ' · <span class="star">★</span> new peak',
        show_deltas=True, show_new_peaks=True,
    )


def _render_card_png(html_content: str, out_path: Path, scale: int = 3) -> None:
    from playwright.sync_api import sync_playwright

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--force-color-profile=srgb"])
        page = browser.new_page(viewport={"width": 2200, "height": 400}, device_scale_factor=scale)
        page.set_content(html_content, wait_until="load")
        card = page.locator("#card")
        card.wait_for(state="visible", timeout=5000)
        card.screenshot(path=str(out_path))
        browser.close()


def _peak_since_release(
    track: dict, source: dict, cache: dict[Path, list[dict]], today: str, exclude_scraped_at: str = "",
) -> int | None:
    """Best (lowest) rank this track has had on this chart across EVERY
    collected cycle since its release day (all rows of each daily CSV, not
    just the latest cycle) — real history only, never inferred. Rows of
    `exclude_scraped_at` (the current cycle) are skipped, so the result is
    the peak BEFORE this cycle (used to detect a NEW PEAK)."""
    released = track.get("release_date")
    try:
        start = released.astimezone(ZoneInfo("Europe/Paris")).date() if released else None
    except Exception:
        start = None
    end = datetime.strptime(today, "%Y-%m-%d").date()
    if start is None or start > end:
        start = end
    best: int | None = None
    day = start
    while day <= end:
        path = source["path_for"](day.strftime("%Y-%m-%d"))
        rows, by_country = _index(path, cache)
        pool = by_country.get(source["country"], []) if source["country"] is not None else rows
        for row in pool:
            if source["row_filter"] and not source["row_filter"](row):
                continue
            if exclude_scraped_at and str(row.get("scraped_at") or "") == exclude_scraped_at:
                continue
            if not _row_matches(track, row):
                continue
            r = _rank_of(row)
            if r is None:
                continue
            if best is None or r < best:
                best = r
        day += timedelta(days=1)
    return best


def _previous_cycle_rank(track: dict, source: dict, cache: dict[Path, list[dict]], today: str, scraped_at: str) -> int | None:
    """Rank at the latest cycle before `scraped_at` (today's CSV, else the
    previous day's), None if the track wasn't on that chart then."""
    day = datetime.strptime(today, "%Y-%m-%d")
    for d in (today, (day - timedelta(days=1)).strftime("%Y-%m-%d")):
        rows, by_country = _index(source["path_for"](d), cache)
        pool = by_country.get(source["country"], []) if source["country"] is not None else rows
        pool = [r for r in pool if str(r.get("scraped_at") or "") < scraped_at
                and (not source["row_filter"] or source["row_filter"](r))]
        if not pool:
            continue
        latest = max(str(r.get("scraped_at") or "") for r in pool)
        _row, rank = _best_match(track, [r for r in pool if str(r.get("scraped_at") or "") == latest])
        return rank
    return None


def _collect_track_placements(track: dict, sources: list[dict], cache: dict[Path, list[dict]], state: dict, today: str) -> list[dict]:
    """One entry per chart/source this track currently appears on, each
    carrying its own delta vs this script's state (not the CSV's own
    `previous_rank`, which means "vs yesterday" for Global and "vs last
    cycle" for country/iTunes — inconsistent for what we need here).

    State = the rank shown by the LAST POSTED card on that chart: it is only
    updated after a successful post (2026-09-24 fix), so a failed or throttled
    post keeps the old baseline and the next cycle re-detects the move and
    retries instead of silently losing it."""
    placements: list[dict] = []
    for source in sources:
        scraped_at, latest_rows = _source_latest_rows(source, cache)
        if not scraped_at:
            continue
        row, rank = _best_match(track, latest_rows)
        if row is None or rank is None:
            continue

        state_key = f"{track['key']}|{source['source_key']}"
        prev = state.get(state_key) or {}
        prev_rank = prev.get("rank")
        cycle_prev = _previous_cycle_rank(track, source, cache, today, scraped_at)
        if prev_rank is None:
            # Never posted here, but maybe already charting before (the Encore
            # album sat on Apple Music album charts from 00:00, 2026-09-25):
            # compare with the previous real cycle, never call it NEW.
            prev_rank = cycle_prev
        delta = (prev_rank - rank) if prev_rank is not None else None
        # Peak before this cycle (real CSV history + state), then this cycle.
        prior = []
        csv_peak = _peak_since_release(track, source, cache, today, exclude_scraped_at=scraped_at)
        if csv_peak is not None:
            prior.append(csv_peak)
        if prev.get("peak"):
            prior.append(int(prev["peak"]))
        prior_peak = min(prior) if prior else None
        peak = min([rank] + prior)
        # Never posted on this chart yet = badge "NEW" (owner 2026-09-24: never
        # "NEW PEAK" on a first appearance). A NEW placement never reads its
        # delta, so a missing baseline can no longer crash the tweet builder.
        is_new = prev_rank is None
        new_peak = (not is_new) and prior_peak is not None and rank < prior_peak
        # RE-PEAK (owner 2026-09-25): back exactly at its peak after the last
        # posted card showed it lower.
        re_peak = ((not is_new) and prior_peak is not None and rank == prior_peak
                   and prev_rank is not None and prev_rank > rank)
        # What THIS cycle brought (song update posts, 2026-09-27): vs the peak
        # before this cycle and the previous cycle — not vs the last post, or a
        # song that rarely posts would pile up stale peaks.
        cycle_new_peak = csv_peak is not None and rank < csv_peak
        cycle_re_peak = csv_peak is not None and rank == csv_peak and cycle_prev is not None and cycle_prev > rank
        if track.get("old"):
            # Out before our history: no real peak, never NEW (album_song_tracks).
            is_new = new_peak = re_peak = cycle_new_peak = cycle_re_peak = False
            peak = None
        placements.append(
            {
                "label": source["chart_label"],
                "platform": source["platform"],
                "region": source["region"],
                "prev_scraped_at": prev.get("scraped_at"),
                "rank": rank,
                "peak": peak,
                "new_peak": new_peak,
                "re_peak": re_peak,
                "cycle_new_peak": cycle_new_peak,
                "cycle_re_peak": cycle_re_peak,
                "cycle_new_no1": rank == 1 and cycle_prev != 1,
                "is_new": is_new,
                "delta": delta,
                # Never posted on this chart = worth a first post even if the
                # rank equals the previous CSV cycle (the Encore album card).
                "moved": prev_rank != rank or not prev,
                "key": source.get("key", True),  # only key markets trigger posts
                "state_key": state_key,
                "scraped_at": scraped_at,
            }
        )
    return placements


def _worldwide_stats(
    track: dict, platform: str, cache: dict[Path, list[dict]], today: str, path: Path | None = None,
) -> dict[str, int]:
    """{no1, top10, charting}: countries (ALL storefronts collected, not only
    the key ones) where the track is #1 / top 10 / on the chart this cycle —
    real CSV rows only. `path` = the express file (release_watch), which
    holds every storefront too."""
    if path is not None:
        pass
    elif platform == "itunes":
        path = itunes_daily_csv(today, "itunes_top_albums.csv" if track.get("is_album") else "itunes_top_songs.csv")
    else:
        path = apple_music_daily_csv(
            today, "apple_music_country_albums.csv" if track.get("is_album") else "apple_music_country_charts.csv"
        )
    _, rows = _source_latest_rows({"path": path, "row_filter": None}, cache)
    best: dict[str, int] = {}
    for row in rows:
        if not _row_matches(track, row):
            continue
        rank = _rank_of(row)
        country = str(row.get("country") or "").lower()
        if rank is not None and country and (country not in best or rank < best[country]):
            best[country] = rank
    return {
        "no1": sum(1 for r in best.values() if r == 1),
        "top10": sum(1 for r in best.values() if r <= 10),
        "charting": len(best),
    }


def _countries(n: int) -> str:
    return f"{n} {'country' if n == 1 else 'countries'}"


def worldwide_sentence(stats: dict[str, int], platform: str, chart: str = "") -> str:
    """"🌍 Now #1 in 5 countries, top 10 in 23 and charting in 61 on iTunes
    worldwide." — tiers equal to the previous one are left out (owner
    2026-09-25: every tweet also says how the song does in the other
    countries). "" when it charts in fewer than 2 countries."""
    no1, top10, charting = stats.get("no1", 0), stats.get("top10", 0), stats.get("charting", 0)
    if charting < 2:
        return ""
    parts = []
    if no1:
        parts.append(f"#1 in {_countries(no1)}")
    if top10 and top10 != no1:
        parts.append(f"top 10 in {top10 if parts else _countries(top10)}")
    if charting != max(no1, top10):
        parts.append(f"charting in {charting if parts else _countries(charting)}")
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + f" and {parts[-1]}"
    return f"🌍 Now {joined} on {chart or PLATFORMS[platform]['label']} worldwide."


def _is_headline_event(p: dict) -> bool:
    """Always worth a post, right away: a debut, a new #1, or a new peak at
    ANY rank on any key chart — even if every other chart dropped the same
    cycle (owner 2026-09-25)."""
    return bool(p["is_new"] or (p["moved"] and p["rank"] == 1) or p["new_peak"])


def post_due(track: dict, moved: list[dict], state: dict, now: datetime) -> str | None:
    """None = post now, else why it waits. The iTunes chart is LIVE (owner
    2026-09-25: "ça change chaque minute"), so a song can go #5 -> #4 -> #3 in
    a quarter of an hour:
      - a debut on a key chart or a new #1: always, right away;
      - another new peak: right away unless this song was posted less than
        PEAK_MIN_GAP_MINUTES ago — then it waits and the next post carries
        every peak reached meanwhile (one post, not three);
      - a plain climb: at most once per MIN_GAP_HOURS; drops: never (unless
        APPLE_MUSIC_DEBUT_POST_DROPS=1)."""
    if any(p["is_new"] or (p["moved"] and p["rank"] == 1) for p in moved):
        return None
    last_post = _parse_release_date(state.get(f"__last_post|{track['key']}") or "")
    since_min = (now - last_post).total_seconds() / 60.0 if last_post else None
    if any(p["new_peak"] for p in moved):
        if since_min is not None and since_min < PEAK_MIN_GAP_MINUTES:
            return f"new peak held, last post {since_min:.0f} min ago (< {PEAK_MIN_GAP_MINUTES:g})"
        return None
    if not any((p["delta"] or 0) > 0 for p in moved) and not POST_DROPS:
        return "only drops"
    if since_min is not None and since_min < MIN_GAP_HOURS * 60:
        return f"minor move, last post < {MIN_GAP_HOURS:g}h ago"
    return None


def song_update(track: dict, placements: list[dict], state: dict, now: datetime) -> dict | None:
    """{"score", "peaks"} when this cycle is a good update for the song (see
    SONG_UPDATE_POSTS), None otherwise — also None while the song was posted
    less than UPDATE_GAP_HOURS ago on this platform."""
    if not SONG_UPDATE_POSTS or track.get("old") or track.get("is_album"):
        return None
    peaks = [p for p in placements
             if p["rank"] <= UPDATE_TOP and (p.get("cycle_new_peak") or p.get("cycle_re_peak"))]
    score = sum(
        ((1.0 if p["cycle_new_peak"] else 0.5) + (1.0 if p.get("cycle_new_no1") else 0.0))
        * (UPDATE_GLOBAL_WEIGHT if p["region"] == "global" else MARKET_WEIGHTS.get(p["region"], MARKET_WEIGHT_DEFAULT))
        for p in peaks
    )
    if len(peaks) < UPDATE_MIN_PEAKS or score < UPDATE_MIN_SCORE:
        return None
    last = _parse_release_date(state.get(f"__last_post|{track['key']}") or "")
    if last and (now - last).total_seconds() / 3600.0 < UPDATE_GAP_HOURS:
        print(f"[new_release_progression] {track['title']}: good update (score {score:.2f}) but posted "
              f"< {UPDATE_GAP_HOURS:g}h ago — not posted")
        return None
    return {"score": score, "peaks": peaks}


def _peak_list(peaks: list[dict]) -> str:
    """"#3 in the US & the UK, #1 in Suriname" — grouped by rank, best first,
    bigger market first on a tie, the Global chart named as such."""
    by_rank: dict[int, list[str]] = {}
    for p in sorted(peaks, key=lambda p: (p["rank"], _market_order(p))):
        by_rank.setdefault(p["rank"], []).append(p["region"])
    parts = []
    for rank, regions in by_rank.items():
        places = ["the Global chart" if r == "global" else MARKET_NAMES.get(r, country_label(r)) for r in regions]
        parts.append(f"#{rank} {'on' if regions == ['global'] else 'in'} {_and_list(places)}")
    return ", ".join(parts)


def build_update_tweet(*, track: dict, update: dict, platform: str, worldwide: dict[str, int]) -> str:
    """Song update post (2026-09-27): the peaks of the cycle, then the
    worldwide line + link. Too long -> the smallest markets go first."""
    from collectors.twitter.albums import album_emoji
    from collectors.twitter.links import amcharts_url

    label = PLATFORMS[platform]["label"]
    url = amcharts_url(PLATFORMS[platform]["site_source"])
    world = worldwide_sentence(worldwide, platform)
    title = f'"{track["title"]}"'
    # Keep the biggest markets when trimming (Global, then market weight).
    kept = sorted(update["peaks"], key=_market_order)
    while True:
        new = [p for p in kept if p["cycle_new_peak"]]
        back = [p for p in kept if not p["cycle_new_peak"]]
        if len(new) == 1 and not back:
            lead = f"{title} hits a new peak of {_peak_list(new)} on {label}."
        elif new:
            lead = f"{title} hits new peaks on {label}: {_peak_list(new)}."
            if back:
                lead = lead[:-1] + f" — and is back at its peak: {_peak_list(back)}."
        else:
            lead = f"{title} is back at its peak on {label}: {_peak_list(back)}."
        tweet = "\n\n".join(x for x in (f"{album_emoji(track.get('emoji_album') or track.get('album'))} | {lead}", world, url) if x)
        if _tweet_weight(tweet, url) <= TEXT_BUDGET or len(kept) <= 1:
            return tweet
        kept.pop()


def _where(p: dict, platform: str, is_album: bool = False) -> str:
    if p["region"] == "global":
        return "on the Global Apple Music chart"
    market = MARKET_NAMES.get(p["region"], p["label"])
    if is_album:
        return f"on the {PLATFORMS[platform]['label']} albums chart in {market}"
    return f"on {PLATFORMS[platform]['label']} in {market}"


def _market_order(p: dict) -> tuple[float, str]:
    """Same-rank tie-break, like the site (frontend utils/marketWeights.js,
    owner 2026-09-25): bigger store first (TayBoard's AM_MARKET_WEIGHTS),
    then country code. Global always leads."""
    region = p["region"]
    if region == "global":
        return (-2.0, "")
    return (-MARKET_WEIGHTS.get(region, MARKET_WEIGHT_DEFAULT), region)


def _short_where(p: dict) -> str:
    return "Global" if p["region"] == "global" else MARKET_NAMES.get(p["region"], p["label"])


def build_tweet_text(*, track: dict, placements: list[dict], platform: str, worldwide: dict[str, int],
                     prefix: str = "") -> str:
    """Tweet format (2026-09-24, replaces "debuts on N iTunes charts — full
    chart breakdown below"): album emoji, the actual rank of the best news in
    the lead, the other notable key markets after it, and the worldwide
    line (worldwide_sentence). Ranks/deltas are in the text so two cycles
    never produce the same tweet."""
    from collectors.twitter.albums import album_emoji
    from collectors.twitter.links import amcharts_url

    # The text talks about the key markets; the other countries are in the
    # card and in the worldwide line.
    placements = [p for p in placements if p.get("key", True)] or placements
    moved = [p for p in placements if p["moved"]]
    # A song carried by a thread without moving (owner 2026-09-25): its best
    # current key-market rank, stated as it is ("is now #N"), never "hits".
    notable = [p for p in moved if _is_headline_event(p) or (p["delta"] or 0) > 0] or moved or list(placements)
    notable.sort(key=lambda p: (0 if _is_headline_event(p) else 1, p["rank"], _market_order(p)))
    lead = notable[0]
    is_album = bool(track.get("is_album"))
    where = _where(lead, platform, is_album)
    if lead["is_new"]:
        headline = f"debuts at #{lead['rank']} {where}"
    elif lead["rank"] == 1 and lead["moved"]:
        headline = f"hits #1 {where}"
    elif lead["new_peak"]:
        headline = f"reaches a new peak of #{lead['rank']} {where}"
    elif (lead["delta"] or 0) > 0:
        headline = f"climbs to #{lead['rank']} {where} (+{lead['delta']})"
    else:
        headline = f"is now #{lead['rank']} {where}"

    others = [p for p in notable[1:] if p["is_new"] or (p["delta"] or 0) > 0 or p["rank"] == 1][:4]
    extras = ""
    if others:
        by_rank: dict[int, list[str]] = {}
        global_part = ""
        for p in sorted(others, key=_market_order):
            if p["region"] == "global":
                global_part = f"#{p['rank']} on the Global chart"  # not "#3 in Global"
            else:
                by_rank.setdefault(p["rank"], []).append(_short_where(p))
        parts = [global_part] if global_part else []
        for rank, names in sorted(by_rank.items()):
            where_list = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" & {names[-1]}"
            parts.append(f"#{rank} in {where_list}")
        extras = " — also " + ", ".join(parts)

    emoji = album_emoji(track.get("emoji_album") or track.get("album"))
    body = f'{emoji} | "{track["title"]}" {headline}{extras}.'
    world = worldwide_sentence(
        worldwide, platform, f"the {PLATFORMS[platform]['label']} albums chart" if is_album else "",
    )
    if world:
        body += f"\n\n{world}"
    url = amcharts_url(PLATFORMS[platform]["site_source"])
    tweet = f"{prefix}{body}\n\n{url}"

    def weighted(text: str) -> int:
        # X weighting: a link counts 23, an emoji 2. twitter.py::_validate_tweet_lengths
        # checks the RAW length against TWITTER_TEXT_LIMIT: the stricter of the two decides.
        link = 23 - len(url) if url in text else 0
        x_weight = len(text) + link + sum(1 for ch in text if ord(ch) > 0x2000)
        return max(x_weight, len(text) - 5)

    if weighted(tweet) > TEXT_BUDGET and prefix:
        # Thread opener (header + best song, owner 2026-09-25 example has no
        # link): the link goes first, then the extras.
        tweet = tweet.replace(f"\n\n{url}", "", 1)
    if weighted(tweet) > TEXT_BUDGET and extras:
        tweet = tweet.replace(extras, "", 1)
    return tweet


def render_card(*, track: dict, placements: list[dict], max_scraped_at: str, platform: str) -> Path:
    html_content = _build_progression_card_html(
        track=track, placements=placements, scraped_at=max_scraped_at, platform=platform,
    )
    out_path = OUT_DIR / f"{_safe_slug(track['key'], platform, max_scraped_at)}.png"
    _render_card_png(html_content, out_path, scale=2)
    return out_path


def _album_snapshot_post(album: str, today: str, state: dict, active_tracks: list[dict]) -> dict | None:
    """Album-filtered Global Apple Music snapshot (generate_snapshot_images
    --album) posted alongside the per-track cards while an album has a track
    in its debut window (owner 2026-09-24). Global only really moves ~once a
    day while this runs hourly -> returns something only when the album's
    Global standings differ from the last ones posted (never the same image
    twice). Also None while NO new track of this album is on the Global chart
    yet (audit 2026-09-24: it would otherwise post a card of old songs only,
    and re-post it every time they move). None = nothing new."""
    import generate_snapshot_images as snap

    try:
        album_name, album_keys = snap._resolve_album_filter(album)
    except ValueError as exc:
        print(f"[new_release_progression] WARN: album snapshot skipped for {album!r}: {exc}")
        return None
    rows, scraped_at = snap.get_region_rows(today, "global", None)
    rows = snap._filter_album(rows, album_keys)
    if not rows or not scraped_at:
        return None
    new_tracks = [t for t in active_tracks if t["album"] == album]
    if not any(_row_matches(t, r) for t in new_tracks for r in rows):
        return None
    signature = {str(r.get("song_name") or ""): snap._rank_int(r.get("rank")) for r in rows}
    state_key = f"album_snapshot|{song_name_key(album_name)}|global"
    if (state.get(state_key) or {}).get("ranks") == signature:
        return None
    return {
        "album": album_name,
        "album_keys": album_keys,
        "scraped_at": scraped_at,
        "signature": signature,
        "state_key": state_key,
        "count": len(rows),
    }


def post_with_retries(tweet: str, image_path: Path, lock_path: Path, *, debut: bool = False,
                      thread: list[tuple[str, Path]] | None = None) -> bool:
    """post_with_image, retried right away (owner 2026-09-25: "si quelque chose
    échoue on réessaie toujours"): up to POST_ATTEMPTS tries, POST_RETRY_WAIT s
    apart, when the failure happened BEFORE the tweet was sent (busy slot,
    browser, composer, image upload). Never retried when X did not confirm a
    click on "Post" — the tweet may be live, a retry could duplicate it: that
    case counts as posted and sends a high alert to check the account.
    False = still failing after every try (next cycle retries again)."""
    import time
    from collectors.spotify.core.twitter import get_last_post_error, post_image_thread, post_with_image

    for attempt in range(1, POST_ATTEMPTS + 1):
        try:
            if thread:
                # Songs thread (owner 2026-09-25): one native thread, each post
                # with its card. X unconfirmed -> same no-repost rule below.
                if lock_path.exists():
                    return True
                ok = post_image_thread(
                    thread, TWITTER_SESSION,
                    priority=DEBUT_POST_PRIORITY if debut else POST_PRIORITY,
                    slot_timeout=_slot_timeout(debut),
                )
            else:
                ok = post_with_image(
                    tweet, image_path, TWITTER_SESSION,
                    priority=DEBUT_POST_PRIORITY if debut else POST_PRIORITY,
                    skip_if=lambda: lock_path.exists(),
                    slot_timeout=_slot_timeout(debut),
                )
            error = "" if ok else (get_last_post_error() or "unknown error")
        except TimeoutError as exc:
            ok, error = False, f"X slot busy: {exc}"
        except Exception as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"
        if ok:
            return True
        if "non confirme" in error:
            alert(f"X did not confirm this post — it may or may not be live, NOT reposted to avoid a "
                  f"duplicate. Check @swiftiescharts: {tweet.splitlines()[0][:120]}")
            return True
        if attempt < POST_ATTEMPTS:
            print(f"[new_release_progression] post attempt {attempt}/{POST_ATTEMPTS} failed ({error}) — "
                  f"retrying in {POST_RETRY_WAIT}s")
            time.sleep(POST_RETRY_WAIT)
        else:
            print(f"[new_release_progression] post failed after {POST_ATTEMPTS} attempts ({error})")
    return False


def _thread_header(entries: list[dict], platform: str) -> str:
    """Owner's format (2026-09-25):
    🧵 | "The Life of a Showgirl: The Encore"'s songs on the Apple Music charts."""
    albums = {e["track"].get("album") for e in entries}
    chart = f"the {PLATFORMS[platform]['label']} charts"
    if len(albums) == 1 and next(iter(albums)):
        album = next(iter(albums))
        # Whole-album thread (2026-09-26): the album's own name, not the new edition's.
        name = album if any(e["track"].get("old") for e in entries) else display_title_for_album(album)
        return f'🧵 | "{name}"\'s songs on {chart}.'
    return f"🧵 | Taylor Swift's new songs on {chart}."


def _post_group(entries: list[dict], platform: str, label: str, args, state: dict, now: datetime) -> None:
    """One post (album card, lone song) or one thread (several songs)."""
    lead = entries[0]
    if len(entries) == 1:
        tweet, lock_path = lead["tweet"], lead["lock_path"]
        thread = None
    else:
        opener = build_tweet_text(
            track=lead["track"], placements=lead["placements"], platform=platform,
            worldwide=lead["worldwide"], prefix=_thread_header(entries, platform) + "\n\n",
        )
        thread = [(opener, lead["image_path"])] + [(e["tweet"], e["image_path"]) for e in entries[1:]]
        tweet = opener
        lock_path = LOCKS_DIR / f"{_safe_slug('thread', platform, lead['max_scraped_at'])}.lock"
        print(f"[new_release_progression] [{label}] THREAD of {len(thread)} post(s):")
        for i, (text, _img) in enumerate(thread, 1):
            print(f"  --- {i}/{len(thread)} ({len(text)} chars)\n{text}")
    if args.dry_run:
        return
    if args.no_post:
        for e in entries:
            print(f"[new_release_progression] --no-post: card {e['image_path']}")
        return
    if lock_path.exists():
        return

    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    debut = any(p["is_new"] for e in entries for p in e["placements"])
    ok = post_with_retries(tweet, lead["image_path"], lock_path, debut=debut, thread=thread)
    if not ok:
        titles = ", ".join(f'"{e["track"]["title"]}"' for e in entries)
        alert(f"Post failed for {titles} [{label}] after {POST_ATTEMPTS} attempts — will retry next cycle.")
        return
    stamp = datetime.utcnow().isoformat()
    lock_path.write_text(stamp, encoding="utf-8")
    for e in entries:
        e["lock_path"].write_text(stamp, encoding="utf-8")
        for p in e["placements"]:
            state[p["state_key"]] = {"rank": p["rank"], "peak": p["peak"],
                                     "scraped_at": p["scraped_at"], "title": e["track"]["title"]}
        state[f"__last_post|{e['track']['key']}"] = now.isoformat()
    save_state(state, platform)


def alert(message: str, *, title: str = "New release posts", priority: str = "high", tags: str = "warning") -> None:
    """Phone alert (ntfy, never raises). ntfy.sh is excluded from WARP."""
    print(f"[new_release_progression] ALERT: {message}")
    try:
        from collectors.spotify.core.notify import send

        send(NTFY_TOPIC, message, title=title, tags=tags, priority=priority)
    except Exception as exc:
        print(f"[new_release_progression] WARN: alert failed: {exc}")


def main() -> None:
    args = parse_args()
    now = _now_paris()
    today = now.strftime("%Y-%m-%d")
    platforms = list(PLATFORMS) if args.platform == "all" else [args.platform]
    for platform in platforms:
        try:
            with platform_lock(platform):
                run_platform(platform, args, now, today)
        except Exception as exc:  # never die silently in the middle of the night
            import traceback

            traceback.print_exc()
            alert(f"{PLATFORMS[platform]['label']} run crashed: {type(exc).__name__}: {exc}")


class platform_lock:
    """OS file lock per platform (2026-09-25): the hourly post step and the
    release watcher's express post can run at the same time; this serializes
    them so neither loses the other's state update. Released by the OS if the
    process dies. Waits up to PLATFORM_LOCK_WAIT seconds."""

    def __init__(self, platform: str):
        self.path = HERE / "tools" / "locks" / f"new_release_progression_{platform}.run.lock"
        self.fh = None

    def __enter__(self):
        import time

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        deadline = time.monotonic() + PLATFORM_LOCK_WAIT
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() > deadline:
                    self.fh.close()
                    raise TimeoutError(f"{self.path.name} still held after {PLATFORM_LOCK_WAIT}s")
                time.sleep(1)

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                import msvcrt

                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self.fh.close()
        return False


def _importance(track: dict, placements: list[dict]) -> tuple:
    """Post order (2026-09-25, "be the first to post"): the biggest news
    first — a #1 in a key market (the US first), then the best rank."""
    order = {c: i for i, c in enumerate(["global", *KEY_COUNTRIES])}
    key = [p for p in placements if p.get("key", True)] or placements
    moved = [p for p in key if p["moved"]] or key
    best = min(moved, key=lambda p: (p["rank"], order.get(p["region"], 99)))
    return (0 if best["rank"] == 1 else 1, best["rank"], order.get(best["region"], 99), 0 if track.get("is_album") else 1)


def _strength(track: dict, placements: list[dict]) -> tuple:
    """Album thread order (2026-09-26, "du plus fort au plus faible"): best
    current rank on a key chart (Global, then the US first on a tie), then
    how many countries it charts in."""
    order = {c: i for i, c in enumerate(["global", *KEY_COUNTRIES])}
    key = [p for p in placements if p.get("key", True)] or placements
    best = min(key, key=lambda p: (p["rank"], order.get(p["region"], 99)))
    return (0 if track.get("is_album") else 1, best["rank"], order.get(best["region"], 99), -len(placements))


def run_platform(
    platform: str, args: argparse.Namespace, now: datetime, today: str, express: dict[str, Path] | None = None,
) -> None:
    """--dry-run: prints the tweets, renders nothing, writes nothing.
    --no-post: renders the cards, prints the tweets, writes nothing (no state,
    no lock) — neither can ever eat a real post anymore (2026-09-24 fix).
    express = {"song": csv, "album": csv} (release_watch.py, iTunes only): the
    key-country rows fetched the second the release shows up, instead of the
    full 168-country snapshot — no worldwide line (we don't have it yet), no
    Global cards. The full run right after only posts what moved since."""
    label = PLATFORMS[platform]["label"]
    writes = not args.dry_run and not args.no_post
    candidates = candidate_tracks(now, args.window_hours)
    if not candidates:
        print(f"[new_release_progression] [{label}] No release candidate.")
        if platform == "apple_music" and not express:
            _post_global_daily(args, today, load_state(platform), [], platform, writes)
        return

    state = load_state(platform)
    sources = [s for s in build_sources(today, express) if s["platform"] == platform]
    if express:
        for s in sources:
            s["path"] = express[s.get("kind", "song")]
            s["path_for"] = (lambda _d, _p=s["path"]: _p)
    cache: dict[Path, list[dict]] = {}

    detected = resolve_released_at(candidates, now, state, sources, cache)
    if detected and writes:
        save_state(state, platform)
        if platform == "itunes":  # one "it's out" alert, from the faster chain
            alert("Release detected: " + ", ".join(detected), title="New release is out",
                  priority="default", tags="tada")
    active_tracks = active_debut_tracks(candidates, now, args.window_hours)
    waiting = [t["title"] for t in candidates if t.get("released_at") is None]
    if waiting:
        print(f"[new_release_progression] [{label}] Not out yet: " + ", ".join(waiting))
    if not active_tracks:
        print(f"[new_release_progression] [{label}] No track inside its debut window.")
        if platform == "apple_music" and not express:
            _post_global_daily(args, today, state, [], platform, writes)
        return
    print(f"[new_release_progression] [{label}] {len(active_tracks)} track(s) in window: "
          + ", ".join(t["title"] for t in active_tracks))

    if writes:
        from collectors.spotify.core.twitter import post_with_image

    song_sources = [s for s in sources if s.get("kind", "song") == "song"]
    album_sources = [s for s in sources if s.get("kind") == "album"]
    items = [(t, song_sources) for t in active_tracks]
    if ALBUM_SONGS_THREAD and not express:  # every song of the album (2026-09-26)
        items += [(t, song_sources) for t in album_song_tracks(active_tracks)]
    if album_sources and ALBUM_CHART_CARDS:  # Top Albums card (owner 2026-09-25, off since 09-26)
        items += [(a, album_sources) for a in album_items(active_tracks)]

    # Pass 1: what is worth posting this cycle.
    to_post: list[tuple[dict, list[dict]]] = []
    idle_songs: list[tuple[dict, list[dict]]] = []  # charting, but nothing worth a post alone
    update_candidates: list[tuple[float, dict, list[dict], dict]] = []
    for track, item_sources in items:
        try:
            placements = _collect_track_placements(track, item_sources, cache, state, today)
            update = None if express else song_update(track, placements, state, now)
            if update:
                update_candidates.append((update["score"], track, placements, update))
            if not placements and track.get("is_album"):
                continue
            if not placements and track.get("old"):
                continue  # not charting: not in the thread (iTunes: "seulement ceux qui chartent")
            if not placements:
                hours_out = (now - track["released_at"]).total_seconds() / 3600.0
                flag = f"__not_found_alerted|{track['key']}"
                if platform == "itunes" and not express and hours_out >= NOT_FOUND_ALERT_HOURS and not state.get(flag):
                    alert(f'"{track["title"]}" is out for {hours_out:.0f}h but on no {label} key chart '
                          f"({', '.join(KEY_COUNTRIES)}). Title/id mismatch or broken feed?")
                    if writes:
                        state[flag] = now.isoformat()
                        save_state(state, platform)
                continue

            moved = [p for p in placements if p["moved"] and p.get("key", True)]
            if not moved:
                # nothing new on a key chart this cycle (decisions 2026-09-24/25)
                if not track.get("is_album"):
                    idle_songs.append((track, placements))
                continue

            # Volume control (post_due). Skipped cycles keep the last posted
            # baseline, so the next post compares against what followers saw.
            # Hourly runs: every move posts, drops and small climbs included
            # (owner 2026-09-25: "since we did threads now we can post
            # everything" — one thread per cycle, not one post per song). The
            # iTunes follow's mid-hour express posts keep post_due (the chart
            # moves every minute: a thread a minute otherwise).
            held = post_due(track, moved, state, now) if (express or not POST_EVERY_MOVE) else None
            if held:
                if not track.get("is_album"):
                    idle_songs.append((track, placements))
                print(f"[new_release_progression] {track['title']} [{label}]: {held} — not posted")
                continue
            if SONG_POSTS_NUMBER_ONE_ONLY and not track.get("is_album") and not any(
                p["rank"] == 1 for p in moved
            ):
                # Owner 2026-09-26: a song posts on its own only when it gets
                # to #1 on a key chart (new #1 / back at #1).
                print(f"[new_release_progression] {track['title']} [{label}]: moved, no new #1 — not posted")
                continue
            to_post.append((track, placements))
        except Exception as exc:
            import traceback

            traceback.print_exc()
            alert(f'{label}: "{track["title"]}" crashed: {type(exc).__name__}: {exc}')

    # Song update post (2026-09-27): the best candidate only, one per platform
    # and cycle; a song already posting for a key #1 keeps that post.
    update_of: dict[str, dict] = {}
    if update_candidates:
        score, track, placements, update = max(update_candidates, key=lambda c: c[0])
        others = ", ".join(f"{t['title']} {s:.2f}" for s, t, _p, _u in update_candidates if t is not track)
        print(f"[new_release_progression] {track['title']} [{label}]: good update, score {score:.2f} — "
              + ", ".join(f"{p['region']}#{p['rank']}{'' if p['cycle_new_peak'] else ' (back)'}" for p in update["peaks"])
              + (f" (not posted this cycle: {others})" if others else ""))
        if not any(t["key"] == track["key"] for t, _p in to_post):
            to_post.append((track, placements))
            update_of[track["key"]] = update
    # A songs thread carries EVERY song of the release (owner 2026-09-25:
    # "since it's a thread every song should go in"): the ones that triggered
    # it first, then the others with their current ranks.
    idle_keys: set[str] = set()
    if SONG_THREADS and any(not t.get("is_album") for t, _ in to_post):
        to_post += idle_songs
        idle_keys = {t["key"] for t, _ in idle_songs}
    # Biggest news first: the thread opener is the best song (owner 2026-09-25).
    # Album thread (2026-09-26): strongest song first, by its best current
    # rank on a key chart, moved or not.
    to_post.sort(key=lambda tp: _strength(*tp) if ALBUM_SONGS_THREAD else _importance(*tp))

    # Pass 2: text + card for everything worth posting, biggest news first.
    ready: list[dict] = []
    for track, placements in to_post:
        try:
            moved = [p for p in placements if p["moved"] and p.get("key", True)]
            max_scraped_at = max(p["scraped_at"] for p in placements)
            lock_path = LOCKS_DIR / f"{_safe_slug(track['key'], platform, max_scraped_at)}.lock"
            # A song only carried by the thread may already have been posted on
            # this cycle: the thread's own lock guards against duplicates.
            if lock_path.exists() and track["key"] not in idle_keys:
                continue
            worldwide = _worldwide_stats(
                track, platform, cache, today,
                path=(express or {}).get("album" if track.get("is_album") else "song"),
            )
            if track["key"] in update_of:
                tweet = build_update_tweet(track=track, update=update_of[track["key"]], platform=platform,
                                           worldwide=worldwide)
            else:
                tweet = build_tweet_text(track=track, placements=placements, platform=platform, worldwide=worldwide)
            print(f"[new_release_progression] {track['title']} [{label}]: "
                  + ("carried by the thread (no move)" if track["key"] in idle_keys
                     else "good update" if track["key"] in update_of
                     else "moved on " + ", ".join(p["label"] for p in moved)))
            print(f"[new_release_progression] TWEET ({len(tweet)} chars):\n{tweet}")
            image_path = None
            if not args.dry_run:
                image_path = render_card(track=track, placements=placements, max_scraped_at=max_scraped_at, platform=platform)
            ready.append({"track": track, "placements": placements, "worldwide": worldwide, "tweet": tweet,
                          "image_path": image_path, "lock_path": lock_path, "max_scraped_at": max_scraped_at})
        except Exception as exc:
            import traceback

            traceback.print_exc()
            alert(f'{label}: "{track["title"]}" crashed: {type(exc).__name__}: {exc}')

    # Pass 3: post. The songs of the cycle go out as ONE thread (owner
    # 2026-09-25): opener = header + the best song, replies = the others. The
    # album card and a lone song stay independent posts.
    songs = [e for e in ready if not e["track"].get("is_album")]
    if not any(e["track"]["key"] not in idle_keys for e in songs):
        # The song that triggered it was skipped (already posted this cycle):
        # never a thread made only of songs that didn't move.
        ready = [e for e in ready if e["track"]["key"] not in idle_keys]
        songs = []
    groups = [[e] for e in ready if e["track"].get("is_album") or len(songs) < 2 or not SONG_THREADS]
    if len(songs) >= 2 and SONG_THREADS:
        groups.append(songs)
    groups.sort(key=lambda g: ready.index(g[0]))
    for group in groups:
        try:
            _post_group(group, platform, label, args, state, now)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            alert(f'{label}: post of "{group[0]["track"]["title"]}" crashed: {type(exc).__name__}: {exc}')

    if express:
        return
    # Global cards = Apple Music data, so they belong to the Apple Music run:
    # the normal Taylor Global card first, then the album-filtered one.
    if platform == "apple_music" and GLOBAL_THREAD:
        _post_global_daily(args, today, state, active_tracks, platform, writes)
    elif platform == "apple_music" and GLOBAL_CARDS:
        try:
            _post_global_snapshot(args, today, state, platform, writes)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            alert(f"Global card crashed: {type(exc).__name__}: {exc}")
    if platform == "apple_music":
        cards = ([(c, None, True) for c in REGION_CARD_COUNTRIES] + [(c, POP_GENRE, True) for c in POP_CARD_COUNTRIES]
                 + [(c, None, False) for c in ENTRY_CARD_COUNTRIES if c not in REGION_CARD_COUNTRIES]
                 + [(c, POP_GENRE, False) for c in ENTRY_POP_COUNTRIES if c not in POP_CARD_COUNTRIES])
        for country, genre, reorder in cards:
            try:
                _post_pop_snapshot(country, args, today, state, active_tracks, platform, writes, genre, reorder)
            except Exception as exc:
                import traceback

                traceback.print_exc()
                alert(f"{genre or 'Region'} card crashed ({country}): {type(exc).__name__}: {exc}")
    if platform == "itunes":
        for album in sorted({t["album"] for t in active_tracks if t["album"]}):
            try:
                _post_itunes_album_thread(album, args, today, state, active_tracks, now, writes)
            except Exception as exc:
                import traceback

                traceback.print_exc()
                alert(f"iTunes album thread crashed ({album}): {type(exc).__name__}: {exc}")
    albums = sorted({t["album"] for t in active_tracks if t["album"]}) if platform == "apple_music" and GLOBAL_CARDS else []
    for album in albums:
        try:
            _post_album_snapshot(album, args, today, state, active_tracks, platform)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            alert(f"Album card crashed ({album}): {type(exc).__name__}: {exc}")


def _post_global_daily(args, today: str, state: dict, active_tracks: list[dict], platform: str, writes: bool) -> None:
    """Global card at every Global update, release window or not (owner
    2026-10-05). Outside a window active_tracks is [] -> plain caption."""
    if not GLOBAL_THREAD:
        return
    try:
        _post_global_thread(args, today, state, active_tracks, platform, writes)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        alert(f"Global thread crashed: {type(exc).__name__}: {exc}")


def _previous_global_signature(today: str, scraped_at: str) -> dict[str, int] | None:
    """{song: rank} of the Global cycle before `scraped_at` (today's CSV, else
    yesterday's latest), None if there is none."""
    day = datetime.strptime(today, "%Y-%m-%d")
    for d in (today, (day - timedelta(days=1)).strftime("%Y-%m-%d")):
        rows = [r for r in _read_today_rows(apple_music_daily_csv(d, "apple_music_global.csv"))
                if str(r.get("scraped_at") or "") < scraped_at]
        if rows:
            latest = max(str(r.get("scraped_at") or "") for r in rows)
            return {str(r.get("song_name") or ""): _rank_of(r) for r in rows if str(r.get("scraped_at") or "") == latest}
    return None


def _post_global_snapshot(args, today: str, state: dict, platform: str, writes: bool) -> None:
    """Normal Global Apple Music card (every Taylor song on the Global chart,
    generate_snapshot_images --region global) posted each time the Global
    standings change while a release is in its window (owner 2026-09-25).
    The first run of the window only records the current standings as the
    baseline (no post): the next change is the first post."""
    import generate_snapshot_images as snap

    rows, scraped_at = snap.get_region_rows(today, "global", None)
    if not rows or not scraped_at:
        return
    signature = {str(r.get("song_name") or ""): snap._rank_int(r.get("rank")) for r in rows}
    state_key = "global_snapshot|global"
    known = state.get(state_key)
    if known is None:
        # First pass: compare with the previous Global cycle in the CSVs, so a
        # change that lands on the first run is posted, not swallowed as the
        # baseline (2026-09-25, the 10:00 Global update).
        prev = _previous_global_signature(today, scraped_at)
        if prev is None or prev == signature:
            if writes:
                state[state_key] = {"ranks": signature, "scraped_at": scraped_at, "baseline": True}
                save_state(state, platform)
            print("[new_release_progression] [Global] baseline recorded (same as the previous Global cycle)")
            return
        known = {"ranks": prev}
    if known.get("ranks") == signature:
        return
    lock_path = LOCKS_DIR / f"{_safe_slug('global_snapshot', scraped_at)}.lock"
    if lock_path.exists():
        return
    print(f"[new_release_progression] [Global] standings changed ({len(rows)} song(s))")

    from collectors.twitter.links import amcharts_url

    tweet = f"🌍 | Taylor Swift songs on the Global Apple Music chart right now:\n\n{amcharts_url('applemusic')}"
    print(f"[new_release_progression] TWEET ({len(tweet)} chars):\n{tweet}")
    if args.dry_run:
        return
    rendered = snap.generate(today, "global", None, OUT_DIR, show_out=True, expect_scraped_at=scraped_at)
    image_path = rendered.replace(OUT_DIR / f"{_safe_slug('global_snapshot', scraped_at)}.png")
    if args.no_post:
        print(f"[new_release_progression] --no-post: card {image_path}")
        return

    from collectors.spotify.core.twitter import post_with_image

    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    ok = post_with_retries(tweet, image_path, lock_path)
    if ok:
        lock_path.write_text(datetime.utcnow().isoformat(), encoding="utf-8")
        state[state_key] = {"ranks": signature, "scraped_at": scraped_at}
        save_state(state, platform)
    else:
        alert("Global card post failed — will retry next cycle.", priority="default")


# Apple Music Pop card per key market (owner 2026-09-25: "US Pop" etc. were
# never posted). Own post, same card as generate_snapshot_images --genre pop.
# ANY song of the new release's album moving UP triggers it, at any rank, old
# tracks of the album included (owner 2026-09-25: "poste toujours dès qu'il y
# a un mouvement positif de n'importe quel track de l'album" — a 21:00 US Pop
# with the Encore songs flat but Opalite +13 / Ophelia +44 didn't post). Album
# = every edition of it (The Life of a Showgirl, : The Encore, Track by Track).
# A drop or an unchanged rank never posts. Moves are vs the same snapshot as
# the card's arrows (last snapshot whose positions differ), so tweet and card
# agree. "new peak" / "debut" only for the new tracks: their Pop history
# starts at release; older tracks' all-time peaks aren't tracked here.
# 2026-09-26 (owner): Pop only for the US and France ("on ne fera plus le pop
# genre, on gardera seulement pour US et FR"), and the same card for the
# normal (all-genre) Apple Music chart of the US ("what about us apple music
# normal pas pop??? il est ou") — same rule, same code, genre None.
# 2026-09-27 (owner): the album-song-moving-up trigger above is replaced by
# NEW_ORDER_ONLY — posted only when the new songs move among themselves,
# inside the top 10 (AM_NEW_ORDER_TOP) (APPLE_MUSIC_DEBUT_NEW_ORDER_ONLY=0 restores it).
POP_GENRE = "Pop"
POP_CARD_COUNTRIES = [
    c.strip().lower() for c in os.getenv("APPLE_MUSIC_DEBUT_POP_COUNTRIES", "us").split(",") if c.strip()
]
REGION_CARD_COUNTRIES = [
    c.strip().lower() for c in os.getenv("APPLE_MUSIC_DEBUT_REGION_CARD_COUNTRIES", "us,au,ca").split(",") if c.strip()
]
# Owner 2026-09-27: "quand une chanson de showgirl (donc l'album sorti pas
# seulement la nouvelle edition) rejoint le us apple music charts alors poste,
# de meme pour les bigs stores". Any song of the album (every edition) joining
# the normal chart of these stores posts that store's album card. Big stores
# = HIGHLIGHT_COUNTRIES (IFPI top 10) + the stores already carded (au); the
# ones outside REGION_CARD_COUNTRIES post on an entry only.
ENTRY_CARD_COUNTRIES = [
    c.strip().lower() for c in os.getenv(
        "APPLE_MUSIC_DEBUT_ENTRY_CARD_COUNTRIES",
        ",".join(dict.fromkeys(HIGHLIGHT_COUNTRIES + REGION_CARD_COUNTRIES)),
    ).split(",") if c.strip()
]
# Same for their Apple Music Pop chart (owner 2026-09-27, "et apple music
# pop?" -> "oui vasy"): US Pop keeps its reorder trigger too, the others post
# on an entry only.
ENTRY_POP_COUNTRIES = [
    c.strip().lower() for c in os.getenv(
        "APPLE_MUSIC_DEBUT_ENTRY_POP_COUNTRIES", ",".join(ENTRY_CARD_COUNTRIES),
    ).split(",") if c.strip()
]


def _chart_name(genre: str | None) -> str:
    return f"Apple Music {genre} chart" if genre else "Apple Music chart"


def _card_kind(genre: str | None) -> str:
    """State/lock prefix: "pop" (kept for the existing Pop state), "region"."""
    return genre.lower() if genre else "region"
POP_TOP_N = 10


def _album_family(name: str) -> str:
    """Edition-free album name: "The Life of a Showgirl: The Encore" and
    "The Life of a Showgirl (Track by Track Version)" -> "the life of a showgirl"."""
    import re

    return re.split(r"\s*[:(\[]", str(name or "").strip().lower(), maxsplit=1)[0].strip()


# Owner 2026-09-27: the chart cards (Apple Music US/AU/CA/US Pop, Global
# thread, iTunes thread) post ONLY when the new songs move among themselves —
# one passes another, or one (re-)enters the chart. An old album song
# climbing, or the 4 new songs all shifting together in the same order, no
# longer posts. 0 = previous rule (any album song moving).
NEW_ORDER_ONLY = os.getenv("APPLE_MUSIC_DEBUT_NEW_ORDER_ONLY", "1") == "1"
# Apple Music only (owner 2026-09-27, "aussi pour apple music, seulement si
# mouvement top 10"): the move among the new songs must touch the top 10 —
# an entry inside it, or a pass where one of the two songs is/was in it.
# iTunes keeps every reorder. 0 = no limit.
AM_NEW_ORDER_TOP = int(os.getenv("APPLE_MUSIC_DEBUT_NEW_ORDER_TOP", "10"))


def _new_song_ranks(tracks: list[dict], rows: list[dict], rank_of=None) -> dict[str, int]:
    """{new track title: best rank} over `rows` (clean + explicit editions:
    the best one). rank_of(row) defaults to the row's own rank; pass the
    previous-snapshot resolver to get where those same rows were before."""
    rank_of = rank_of or _rank_of
    out: dict[str, int] = {}
    for track in tracks:
        ranks = [r for r in (rank_of(row) for row in rows if _row_matches(track, row)) if r is not None]
        if ranks:
            out[track["title"]] = min(ranks)
    return out


def _signature_rows(signature: dict[str, int] | None) -> list[dict]:
    """{song: rank} signature (Global / iTunes album card state) as rows."""
    return [{"song_name": song, "rank": rank} for song, rank in (signature or {}).items()]


def new_songs_moves(cur: dict[str, int], prev: dict[str, int], top: int = 0) -> list[dict]:
    """How the new songs moved among themselves, one entry per song that moved,
    best current rank first: {"title", "rank", "prev" (None = entered),
    "passed": [titles it is now ahead of]}. `cur`/`prev` = {title: rank}. A
    new song (re-)entering counts; one leaving the chart doesn't. top > 0:
    only moves touching the top `top` (entry inside it; pass where one of the
    two songs is or was inside it)."""
    inside = (lambda *ranks: min(ranks) <= top) if top > 0 else (lambda *ranks: True)
    moves = [{"title": t, "rank": cur[t], "prev": None, "passed": []}
             for t in cur if t not in prev and inside(cur[t])]
    both = [t for t in cur if t in prev]
    now_order = sorted(both, key=lambda t: cur[t])
    before = sorted(both, key=lambda t: prev[t])
    for i, a in enumerate(now_order):
        passed = [b for b in now_order[i + 1:]
                  if before.index(b) < before.index(a) and inside(cur[a], cur[b], prev[a], prev[b])]
        if passed:
            moves.append({"title": a, "rank": cur[a], "prev": prev[a], "passed": passed})
    return sorted(moves, key=lambda m: m["rank"])


def new_songs_order_change(cur: dict[str, int], prev: dict[str, int], top: int = 0) -> str | None:
    """new_songs_moves as log text, None when nothing moved."""
    return ", ".join(
        f'"{m["title"]}" enters at #{m["rank"]}' if m["prev"] is None
        else f'"{m["title"]}" passes ' + ", ".join(f'"{b}"' for b in m["passed"])
        for m in new_songs_moves(cur, prev, top)
    ) or None


def _tweet_weight(text: str, url: str = "") -> int:
    """X weighting (a link counts 23, an emoji 2) — and never over the raw
    length twitter.py::_validate_tweet_lengths checks: the stricter decides."""
    link = 23 - len(url) if url and url in text else 0
    return max(len(text) + link + sum(1 for ch in text if ord(ch) > 0x2000), len(text) - 5)


def _and_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" & {items[-1]}"


def _move_sentence(move: dict, badge: str, where: str, debut: bool) -> str:
    """Caption line for one new song that moved among the new songs (owner
    2026-09-27: "on le dit dans la caption aussi si elle atteint un peak").
    badge = the card's Peak badge for that song ("NEW PEAK", "RE-PEAK", ...).
    where = "" for the 2nd+ sentence (the 1st one already says the chart)."""
    title, rank = f'"{move["title"]}"', move["rank"]
    w = f" {where}" if where else ""
    if move["prev"] is None:
        return f"{title} {'debuts' if debut else 're-enters'} at #{rank}{w}."
    passed = _and_list([f'"{t}"' for t in move["passed"]])
    if rank == 1:
        return f"{title} passes {passed} to take #1{w}!"
    if badge == "NEW PEAK":
        return f"{title} passes {passed} and hits a new peak of #{rank}{w}."
    if badge == "RE-PEAK":
        return f"{title} passes {passed} and returns to its peak of #{rank}{w}."
    if rank < move["prev"]:
        return f"{title} passes {passed} and climbs to #{rank}{w}."
    return f"{title} moves ahead of {passed} at #{rank}{w}."


def _base_song_key(title: object) -> str:
    """Title key without its trailing version suffix. Apple swaps which
    version of a song it charts (US 2026-09-26: "The Fate of Ophelia (Track
    by Track)" #21 at 13:25 -> "The Fate of Ophelia" #10 at 18:00): the song
    never left, so that's no entry."""
    import re

    return re.sub(r"\s*[(\[][^()\[\]]*[)\]]\s*$", "", song_name_key(title)).strip()


def album_entries(album_rows: list[dict], prev_rank_of, prev_titles: set[str] | None) -> list[dict]:
    """Album songs on the chart now of which no version was on the card's
    previous snapshot: [{"title", "rank", "row"}], one per song (clean /
    explicit editions), best rank first. prev_rank_of = the card's resolver
    (Apple id first, id swap fallback); prev_titles = title keys of that
    snapshot, None = no previous snapshot -> nothing (no baseline, no claim)."""
    if prev_titles is None:
        return []
    before = {_base_song_key(t) for t in prev_titles}
    out: list[dict] = []
    seen: set[str] = set()
    for row in sorted(album_rows, key=lambda r: _rank_of(r) or 10**6):
        rank, title = _rank_of(row), str(row.get("song_name") or "").strip()
        base = _base_song_key(title)
        if rank is None or not title or base in seen:
            continue
        seen.add(base)
        if prev_rank_of(row) is None and base not in before:
            out.append({"title": title, "rank": rank, "row": row})
    return out


def _entries_sentence(entries: list[dict], where: str, debut: bool, cap: int) -> str:
    """Caption line for album songs joining the chart (all debuts, or all
    re-entries). where = "" after the first sentence. cap = titles listed
    before "& N more songs"."""
    if len(entries) == 1:
        e = entries[0]
        return _move_sentence({"title": e["title"], "rank": e["rank"], "prev": None, "passed": []}, "", where, debut)
    shown = [f'"{e["title"]}" (#{e["rank"]})' for e in entries[:max(cap, 1)]]
    rest = len(entries) - len(shown)
    names = (", ".join(shown) + f" & {rest} more song{'s' if rest > 1 else ''}") if rest else _and_list(shown)
    return f"{names} {'debut' if debut else 'are back'} {where or 'too'}."


def reorder_caption(*, prefix: str, sentences: list[str], standing: str, url: str, budget: int = 0) -> str:
    """`<prefix> | <move sentences>` + the card's intro line + link. Too long
    -> the extra move sentences go first (best-ranked mover kept), then the
    intro line, then the link."""
    budget = budget or TEXT_BUDGET
    sentences = list(sentences)
    while True:
        parts = [f"{prefix} | {' '.join(sentences)}", standing, url]
        tweet = "\n\n".join(p for p in parts if p)
        if _tweet_weight(tweet, url) <= budget or len(sentences) <= 1:
            break
        sentences.pop()
    for drop in ("standing", "url"):
        if _tweet_weight(tweet, url) <= budget:
            break
        if drop == "standing":
            standing = ""
        else:
            url = ""
        tweet = "\n\n".join(p for p in (f"{prefix} | {' '.join(sentences)}", standing, url) if p)
    return tweet


def _top_run(ranks) -> int:
    """How many of the top spots the album holds from #1 down (#1-#4 -> 4)."""
    ranks, run = set(ranks), 0
    while run + 1 in ranks:
        run += 1
    return run


def _flag_emoji(region: str) -> str | None:
    """Tweet prefix for a single-region post (owner 2026-09-25): the country
    flag, built from the two regional-indicator letters of the storefront code
    (`gb` -> 🇬🇧, never "UK"). Windows fonts show these as the letters "US",
    X renders the flag. None when the code isn't two letters (caller falls
    back to the album emoji)."""
    code = (region or "").strip().upper()
    if len(code) != 2 or not code.isascii() or not code.isalpha():
        return None
    return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code)


def _pop_events(active_tracks: list[dict], rows: list[dict], prev_rank_of,
                state: dict, country: str, kind_prefix: str = "pop") -> tuple[list[dict], dict]:
    """(events, new per-track state) for one country's current Pop chart.
    Every row of the new tracks' album(s) is checked against `prev_ranks` (the
    card's previous snapshot). State per new track = {"rank", "peak"}."""
    import generate_snapshot_images as snap

    events: list[dict] = []
    updates: dict[str, dict] = {}
    for track in active_tracks:
        _row, rank = _best_match(track, rows)
        key = f"{kind_prefix}|{song_name_key(track['title'])}|{country}"
        prev = state.get(key) or {}
        if rank is None:
            if prev:
                updates[key] = {"rank": None, "peak": prev.get("peak")}
            continue
        peak = rank if prev.get("peak") is None else min(prev["peak"], rank)
        updates[key] = {"rank": rank, "peak": peak}
    families = {_album_family(t.get("album")) for t in active_tracks} - {""}
    # The normal country CSV has no album_name column: song keys of every
    # edition of the album(s), from the discography (same as the album cards).
    album_keys: set[str] = set()
    for album in {str(t.get("album") or "").split(":")[0].split("(")[0].strip() for t in active_tracks} - {""}:
        try:
            album_keys |= set(snap._resolve_album_filter(album)[1] or ())
        except Exception:
            pass
    for row in rows:
        rank = _rank_of(row)
        title = str(row.get("song_name") or "").strip()
        if rank is None or not title:
            continue
        new_track = next((t for t in active_tracks if _row_matches(t, row)), None)
        # Exactly the rows the album-filtered card shows (2026-09-26: the card
        # IS the post now — a Track by Track climb must not trigger a card it
        # isn't on). Album family only when the discography can't resolve it.
        if album_keys:
            in_album = bool(snap._filter_album([row], album_keys))
        else:
            in_album = _album_family(row.get("album_name")) in families
        if not new_track and not in_album:
            continue  # other albums never trigger
        prev_rank = prev_rank_of(row)  # id first, title fallback on an Apple id swap
        if prev_rank is not None and rank >= prev_rank:
            continue  # unchanged or down: never posts
        prev_peak = None
        if new_track:
            prev_peak = (state.get(f"{kind_prefix}|{song_name_key(new_track['title'])}|{country}") or {}).get("peak")
        if rank == 1 and prev_rank != 1:
            kind = "number_one"
        elif prev_rank is None:
            kind = "debut" if new_track and prev_peak is None else "reentry"
        elif new_track and prev_peak is not None and rank < prev_peak:
            kind = "new_peak"
        elif prev_rank > POP_TOP_N >= rank:
            kind = "top10_entry"
        else:
            kind = "climb"
        track = new_track or {"title": title, "album": str(row.get("album_name") or "")}
        events.append({"track": track, "rank": rank, "kind": kind,
                       "delta": (prev_rank - rank) if prev_rank is not None else None})
    # Strong events first; re-entries and climbs together, by rank.
    order = {"number_one": 0, "debut": 1, "new_peak": 2, "top10_entry": 3, "reentry": 4, "climb": 4}
    events.sort(key=lambda e: (order[e["kind"]], e["rank"]))
    return events, updates


def _pop_event_sentence(event: dict, where: str, chart: str = "Apple Music Pop chart") -> str:
    title = f'"{event["track"]["title"]}"'
    if event["kind"] == "number_one":
        return f"{title} is now #1 on the {chart} in {where}!"
    if event["kind"] == "new_peak":
        return f"{title} hits a new peak of #{event['rank']} on the {chart} in {where}."
    if event["kind"] == "debut":
        return f"{title} debuts at #{event['rank']} on the {chart} in {where}."
    if event["kind"] == "reentry":
        return f"{title} re-enters the {chart} in {where} at #{event['rank']}."
    if event["kind"] == "climb":
        return f"{title} climbs to #{event['rank']} on the {chart} in {where} (+{event['delta']})."
    return f"{title} enters the top 10 of the {chart} in {where} at #{event['rank']}."


def _post_pop_snapshot(country: str, args, today: str, state: dict, active_tracks: list[dict],
                       platform: str, writes: bool, genre: str | None = POP_GENRE, reorder: bool = True) -> None:
    """One country's genre card (Pop) or, genre None, its normal chart card.
    Posted when the new songs reorder (NEW_ORDER_ONLY) or when an album song
    joins the chart (normal chart: ENTRY_CARD_COUNTRIES, Pop:
    ENTRY_POP_COUNTRIES). reorder False (big stores carded on entries only):
    the entry trigger alone."""
    import generate_snapshot_images as snap

    rows, scraped_at = snap.get_region_rows(today, country, genre)
    if not rows or not scraped_at:
        return
    # An hour Apple didn't refresh repeats the last chart, and the moves are
    # vs the last DIFFERENT snapshot, so they'd repeat too: the chart content
    # already handled is remembered and never handled twice.
    kind = _card_kind(genre)
    chart = _chart_name(genre)
    posted_key = f"{kind}_posted|{country}"
    signature = json.dumps(sorted((snap._day_cycles(today, country, genre).get(scraped_at) or {}).items()))
    if state.get(posted_key) == signature:
        return
    prev_ranks, prev_at = snap.get_previous_snapshot_ranks(today, country, genre, scraped_at)
    prev_rank_of = snap.id_swap_prev_rank(prev_ranks, prev_at, country, genre, rows)
    events, updates = _pop_events(active_tracks, rows, prev_rank_of, state, country, kind)
    updates[posted_key] = signature
    label = f"[new_release_progression] [{genre or 'Chart'} {country}]"
    moves = ", ".join(f"{e['track']['title']} {e['kind']} #{e['rank']}" for e in events)
    new_cur = _new_song_ranks(active_tracks, rows)
    new_prev = _new_song_ranks(active_tracks, rows, prev_rank_of)
    album = next((t["album"] for t in active_tracks if t.get("album")), "")
    album_filter = snap._resolve_album_filter(album) if album else None
    album_keys = album_filter[1] if album_filter else None
    entries: list[dict] = []
    entry_countries = ENTRY_POP_COUNTRIES if genre == POP_GENRE else ENTRY_CARD_COUNTRIES if genre is None else []
    if country in entry_countries and album_keys and prev_at:
        # Same baseline as the card's arrows and OUT row.
        entries = album_entries(snap._filter_album(rows, album_keys), prev_rank_of,
                                set(snap._cycle_title_ids(prev_at, country, genre)))
    entry_reason = ", ".join(f'"{e["title"]}" joins at #{e["rank"]}' for e in entries) or None
    if not reorder:
        reason = entry_reason
    elif NEW_ORDER_ONLY:
        # Same baseline as the card's arrows (previous distinct snapshot).
        order_reason = new_songs_order_change(new_cur, new_prev, top=AM_NEW_ORDER_TOP)
        reason = ", ".join(r for r in (order_reason, entry_reason) if r) or None
        if not reason and events:
            print(f"{label} {len(events)} move(s) ({moves}), no move among the new songs "
                  f"in the top {AM_NEW_ORDER_TOP} and no album song joining — not posted")
    else:
        reason = (f"{len(events)} event(s): {moves}" if events else None) or entry_reason
    if not reason:
        # Nothing to post: just track peaks + mark this chart as handled.
        if writes:
            state.update(updates)
            save_state(state, platform)
        return
    lock_path = LOCKS_DIR / f"{_safe_slug(f'{kind}_snapshot', country, scraped_at)}.lock"
    if lock_path.exists():
        return
    where = MARKET_NAMES.get(country, country_label(country))
    print(f"{label} {reason}")

    from collectors.twitter.albums import album_emoji
    from collectors.twitter.links import amcharts_url

    # Album-filtered card + plain caption (owner 2026-09-26: "on poste seulement
    # le filtre showgirl genre '"The Life of a Showgirl: The Encore" songs on
    # the Apple Music XX'"), like the Global album card.
    prefix = _flag_emoji(country) or album_emoji(album, fallback="📈")
    display = display_title_for_album(album) if album else "Taylor Swift"
    url = amcharts_url("applemusic")
    tweet = f'{prefix} | "{display}" songs on the {chart} in {where} right now:\n\n{url}'
    moves = new_songs_moves(new_cur, new_prev, top=AM_NEW_ORDER_TOP) if NEW_ORDER_ONLY and reorder else []
    # A new song entering the top 10 is already a move: said once.
    moved_in = [t for m in moves if m["prev"] is None for t in active_tracks if t["title"] == m["title"]]
    entries = [e for e in entries if not any(_row_matches(t, e["row"]) for t in moved_in)]
    if moves or entries:
        # Say what moved / joined, with the card's own Peak badge (owner 2026-09-27).
        peak_of = snap.make_peak_resolver(today, country, genre, scraped_at)
        units = []  # (rank, where -> sentence), best-ranked news first
        for move in moves:
            track = next(t for t in active_tracks if t["title"] == move["title"])
            row, rank = _best_match(track, rows)
            badge = peak_of(row, rank)[1] if row is not None else ""
            units.append((move["rank"], lambda w, m=move, b=badge: _move_sentence(m, b, w, debut=b == "NEW")))
        debuts = [e for e in entries if peak_of(e["row"], e["rank"])[1] == "NEW"]
        backs = [e for e in entries if e not in debuts]
        album_ranks = [_rank_of(r) for r in snap._filter_album(rows, album_keys)]
        run = _top_run(album_ranks)
        standing = (f'"{display}" songs hold the top {run} right now:' if run >= 3
                    else f'"{display}" songs on the chart right now:')

        def sentences(cap: int) -> list[str]:
            groups = [(g[0]["rank"], lambda w, g=g, d=d: _entries_sentence(g, w, d, cap))
                      for g, d in ((debuts, True), (backs, False)) if g]
            ordered = sorted(units + groups, key=lambda u: u[0])
            return [render(f"on the {chart} in {where}" if i == 0 else "") for i, (_r, render) in enumerate(ordered)]

        # Many songs joining at once (US 2026-09-25 22:00: 7): list fewer
        # titles ("& 5 more songs") before any sentence is dropped.
        cap = max(len(debuts), len(backs), 1)
        while cap > 1 and _tweet_weight(f"{prefix} | {' '.join(sentences(cap))}\n\n{standing}\n\n{url}", url) > TEXT_BUDGET:
            cap -= 1
        tweet = reorder_caption(prefix=prefix, sentences=sentences(cap), standing=standing, url=url)
    print(f"[new_release_progression] TWEET ({len(tweet)} chars):\n{tweet}")
    if args.dry_run:
        return
    rendered = snap.generate(today, country, genre, OUT_DIR, album=album_filter, show_out=True,
                             expect_scraped_at=scraped_at)
    image_path = rendered.replace(OUT_DIR / f"{_safe_slug(f'{kind}_snapshot', country, scraped_at)}.png")
    if args.no_post:
        print(f"[new_release_progression] --no-post: card {image_path}")
        return

    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    ok = post_with_retries(tweet, image_path, lock_path)
    if ok:
        # State only moves once posted: a busy X slot retries next cycle.
        lock_path.write_text(datetime.utcnow().isoformat(), encoding="utf-8")
        state.update(updates)
        save_state(state, platform)
    else:
        alert(f"{chart} card post failed ({country}) — will retry next cycle.", priority="default")


def _post_global_thread(args, today: str, state: dict, active_tracks: list[dict], platform: str, writes: bool) -> None:
    """The normal Global card, ONE post, at every Global update (owner
    2026-09-27: "keep posting but remove the album filter thing so just one
    post"). GLOBAL_ALBUM_CARD=1 brings back the 2026-09-26 thread: + the
    Global album card as a reply ("garde les finalement mais genre dans un
    thread"), only while a new track of the album is on the Global chart."""
    import generate_snapshot_images as snap
    from collectors.twitter.albums import album_emoji
    from collectors.twitter.links import amcharts_url

    rows, scraped_at = snap.get_region_rows(today, "global", None)
    if not rows or not scraped_at:
        return
    signature = {str(r.get("song_name") or ""): snap._rank_int(r.get("rank")) for r in rows}
    state_key = "global_snapshot|global"
    known = state.get(state_key)
    if known is None:
        # First pass: vs the previous Global cycle in the CSVs (see _post_global_snapshot).
        prev = _previous_global_signature(today, scraped_at)
        known = {"ranks": prev} if prev is not None else {"ranks": signature}
    global_changed = known.get("ranks") != signature
    album_posts = []
    if GLOBAL_ALBUM_CARD:
        album_posts = [p for p in (_album_snapshot_post(a, today, state, active_tracks)
                                   for a in sorted({t["album"] for t in active_tracks if t["album"]})) if p]
        # Only a NEW Apple snapshot counts: a different album signature on the SAME
        # scraped_at means our filter changed, not the chart (2026-09-26 13:26: the
        # Track by Track matching fix added a row to the 10:00 Global album card and
        # re-posted a chart unchanged since 10:00).
        album_posts = [p for p in album_posts
                       if (state.get(p["state_key"]) or {}).get("scraped_at") != p["scraped_at"]]
    # Owner 2026-09-27: the Global keeps posting at EVERY update (exempt from the
    # new-songs-reorder rule); a reorder of the new songs only changes the caption.
    new_cur = _new_song_ranks(active_tracks, rows)
    new_prev = _new_song_ranks(active_tracks, _signature_rows(known.get("ranks")))
    moves = new_songs_moves(new_cur, new_prev, top=AM_NEW_ORDER_TOP)
    if not global_changed and not album_posts:
        return
    lock_path = LOCKS_DIR / f"{_safe_slug('global_thread', scraped_at)}.lock"  # name kept: same cycles
    if lock_path.exists():
        return
    # With the album card on, the thread carries both cards even if only one changed.
    if GLOBAL_ALBUM_CARD and not album_posts:
        for album in sorted({t["album"] for t in active_tracks if t["album"]}):
            p = _album_snapshot_post(album, today, {}, active_tracks)
            if p:
                album_posts.append(p)
    link = amcharts_url("applemusic")
    opener = f"🌍 | Taylor Swift songs on the Global Apple Music chart right now:\n\n{link}"
    if moves:
        # The caption says what moved, with the card's Peak badge (owner 2026-09-27).
        peak_of = snap.make_peak_resolver(today, "global", None, scraped_at)
        sentences = []
        for move in moves:
            track = next(t for t in active_tracks if t["title"] == move["title"])
            row, rank = _best_match(track, rows)
            badge = peak_of(row, rank)[1] if row is not None else ""
            sentences.append(_move_sentence(move, badge, "" if sentences else "on the Global Apple Music chart",
                                            debut=badge == "NEW"))
        opener = reorder_caption(prefix="🌍", sentences=sentences,
                                 standing="Taylor Swift songs on the Global chart right now:", url=link)
    posts = [(opener, None)]
    for p in album_posts:
        posts.append((f'{album_emoji(p["album"])} | "{display_title_for_album(p["album"])}" songs on the '
                      f"Global Apple Music chart right now:\n\n{link}", p))
    print(f"[new_release_progression] [Global] {len(posts)} post(s) "
          f"(Global changed: {global_changed}, album changed: {bool(album_posts) and any((state.get(p['state_key']) or {}).get('ranks') != p['signature'] for p in album_posts)})")
    for text, _p in posts:
        print(f"  ---\n{text}")
    if args.dry_run:
        return
    images = []
    rendered = snap.generate(today, "global", None, OUT_DIR, show_out=True, expect_scraped_at=scraped_at)
    images.append(rendered.replace(OUT_DIR / f"{_safe_slug('global_snapshot', scraped_at)}.png"))
    for p in album_posts:
        rendered = snap.generate(today, "global", None, OUT_DIR, album=(p["album"], p["album_keys"]), show_out=True,
                                 expect_scraped_at=p["scraped_at"])
        images.append(rendered.replace(OUT_DIR / f"{_safe_slug('album_snapshot', p['album'], p['scraped_at'])}.png"))
    if args.no_post or not writes:
        print(f"[new_release_progression] --no-post: cards {', '.join(str(i) for i in images)}")
        return
    thread = [(text, img) for (text, _p), img in zip(posts, images)]
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    ok = post_with_retries(thread[0][0], thread[0][1], lock_path, thread=thread if len(thread) > 1 else None)
    if not ok:
        alert("Global thread post failed — will retry next cycle.", priority="default")
        return
    lock_path.write_text(datetime.utcnow().isoformat(), encoding="utf-8")
    state[state_key] = {"ranks": signature, "scraped_at": scraped_at}
    for p in album_posts:
        state[p["state_key"]] = {"ranks": p["signature"], "scraped_at": p["scraped_at"]}
    save_state(state, platform)


def _post_album_snapshot(album: str, args, today: str, state: dict, active_tracks: list[dict], platform: str) -> None:
    pending = _album_snapshot_post(album, today, state, active_tracks)
    if not pending:
        return
    lock_path = LOCKS_DIR / f"{_safe_slug('album_snapshot', pending['album'], pending['scraped_at'])}.lock"
    if lock_path.exists():
        return
    print(f"[new_release_progression] {pending['album']} [Global album snapshot]: standings changed ({pending['count']} song(s))")

    import generate_snapshot_images as snap
    from collectors.twitter.albums import album_emoji
    from collectors.twitter.links import amcharts_url

    tweet = (
        f'{album_emoji(pending["album"])} | "{display_title_for_album(pending["album"])}" songs on the '
        f"Global Apple Music chart right now:"
        f"\n\n{amcharts_url('applemusic')}"
    )
    print(f"[new_release_progression] TWEET ({len(tweet)} chars):\n{tweet}")
    if args.dry_run:
        return

    rendered = snap.generate(
        today, "global", None, OUT_DIR, album=(pending["album"], pending["album_keys"]), show_out=True,
        expect_scraped_at=pending["scraped_at"],
    )
    # one PNG per cycle, like the per-track cards (generate() names by album only)
    image_path = rendered.replace(OUT_DIR / f"{_safe_slug('album_snapshot', pending['album'], pending['scraped_at'])}.png")
    if args.no_post:
        print(f"[new_release_progression] --no-post: card {image_path}")
        return

    from collectors.spotify.core.twitter import post_with_image

    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    ok = post_with_retries(tweet, image_path, lock_path)
    if ok:
        # Recorded only once posted: a busy X slot retries next cycle.
        lock_path.write_text(datetime.utcnow().isoformat(), encoding="utf-8")
        state[pending["state_key"]] = {"ranks": pending["signature"], "scraped_at": pending["scraped_at"]}
        save_state(state, platform)
    else:
        alert(f"Album card post failed ({pending['album']}) — will retry next cycle.", priority="default")


# iTunes US album card (owner 2026-09-25: "le post où on prend dans le US
# iTunes seulement les chansons de Showgirl — là 5 chansons sont dans le top 5").
# Stores the iTunes thread carries (owner 2026-09-28: "je veux que dans le
# thread on mets les 11 stores" — the big stores list, 5 key countries until
# then). cn / kr have no iTunes music store (feeds always empty): left out.
ITUNES_ALBUM_CARD_REGIONS = [c.strip() for c in os.getenv(
    "ITUNES_ALBUM_CARD_REGIONS", ",".join(c for c in ENTRY_CARD_COUNTRIES if c not in ITUNES_NO_MUSIC_STORE),
).split(",") if c.strip()]
# Stores whose new-song reorder TRIGGERS the thread: still the 5 key countries
# (volume unchanged — 26-27/09: 13 threads in 24 cycles vs 18 on all 9).
ITUNES_THREAD_TRIGGER_REGIONS = [c.strip() for c in os.getenv(
    "ITUNES_THREAD_TRIGGER_REGIONS", ",".join(KEY_COUNTRIES),
).split(",") if c.strip()]
ITUNES_ALBUM_CARD_GAP_MINUTES = float(os.getenv("ITUNES_ALBUM_CARD_GAP_MINUTES", "60"))
# Owner 2026-09-29 ("pour itunes, on est le 4eme jour de sortie, on ne postera
# seulement les RE-PEAK ou NEW PEAK"): from the 4th day of the release (release
# day = day 1, Paris calendar days) an iTunes card posts ONLY when a new song
# of the album is at a NEW PEAK / RE-PEAK in that store — a reorder alone no
# longer does. Any of the ITUNES_ALBUM_CARD_REGIONS stores can trigger the
# thread, and the thread carries only the stores with a peak. 0 = off.
ITUNES_PEAK_ONLY_FROM_DAY = int(os.getenv("ITUNES_PEAK_ONLY_FROM_DAY", "4"))


def itunes_peak_only(active_tracks: list[dict], album: str, now: datetime) -> bool:
    """True once the album's first new track is out for ITUNES_PEAK_ONLY_FROM_DAY
    days (day 1 = its release day, Paris time)."""
    if ITUNES_PEAK_ONLY_FROM_DAY <= 0:
        return False
    paris = ZoneInfo("Europe/Paris")
    released = [t["released_at"] for t in active_tracks if t["album"] == album and t.get("released_at")]
    if not released:
        return False
    day1 = min(released).astimezone(paris).date()
    return (now.astimezone(paris).date() - day1).days + 1 >= ITUNES_PEAK_ONLY_FROM_DAY
ITUNES_HEADER_BG = "linear-gradient(135deg,#ff5c6d 0%,#d17cad 55%,#9b5de5 100%)"


# iTunes thread order, biggest change first (owner 2026-09-27).
THREAD_ORDER = {"reorder": 0, "number_one": 1, "peak": 2, "reentry": 3, "up": 4, "down": 5, "none": 6}


def _peak_sentence(entry: dict, where: str) -> str:
    """A new song at a peak without passing another new song: "is back at #1",
    "hits #1 for the first time", "hits a new peak of #N", "returns to its
    peak of #N". where = "" for the 2nd+ sentence."""
    title, rank, w = f'"{entry["song"]}"', entry["rank"], f" {where}" if where else ""
    if rank == 1:
        return (f"{title} hits #1{w} for the first time!" if entry["peak_badge"] == "NEW PEAK"
                else f"{title} is back at #1{w}!")
    if entry["peak_badge"] == "NEW PEAK":
        return f"{title} hits a new peak of #{rank}{w}."
    return f"{title} returns to its peak of #{rank}{w}."


def itunes_thread_header(display: str) -> str:
    return f'🧵 | "{display}" songs on the iTunes charts.'


def _itunes_region_cycles(day: str, region: str) -> list[tuple[str, list[dict]]]:
    """[(scraped_at, rows)] of one country's iTunes Top Songs on `day`, oldest first."""
    by_at: dict[str, list[dict]] = {}
    for r in _read_today_rows(itunes_daily_csv(day, "itunes_top_songs.csv")):
        if str(r.get("country") or "").lower() == region:
            by_at.setdefault(str(r.get("scraped_at") or ""), []).append(r)
    return sorted(by_at.items())


def _album_signature(rows: list[dict], album_keys: set[str]) -> tuple[dict[str, int], dict[str, dict]]:
    """{song: best rank} of the album's songs (iTunes lists explicit + clean
    editions: the best one is kept) + the row behind each."""
    import generate_snapshot_images as snap

    best: dict[str, dict] = {}
    for r in snap._filter_album(rows, album_keys):
        rank = _rank_of(r)
        song = str(r.get("song_name") or "").strip()
        if rank is not None and (song not in best or rank < _rank_of(best[song])):
            best[song] = r
    return {s: _rank_of(r) for s, r in best.items()}, best


def _itunes_history_peaks(region: str, album_keys: set[str], before: str, today: str) -> tuple[dict[str, int], str]:
    """{song: best rank in `region`} over every iTunes snapshot before `before`
    (our iTunes history starts 2026-09-09) + that first day, for the "since"
    label of songs released before it."""
    day_dir = itunes_daily_csv(today, "itunes_top_songs.csv").parent
    root = day_dir.parents[1]
    days = sorted(p.name for p in root.glob("*/*") if p.is_dir() and len(p.name) == 10 and p.name <= today)
    peaks: dict[str, int] = {}
    for day in days:
        for at, rows in _itunes_region_cycles(day, region):
            if at >= before:
                continue
            for song, rank in _album_signature(rows, album_keys)[0].items():
                peaks[song] = min(rank, peaks.get(song, rank))
    return peaks, (days[0] if days else today)


def _post_itunes_album_card(album: str, args, today: str, state: dict, active_tracks: list[dict],
                            now: datetime, writes: bool, region: str = "us",
                            collect: list | None = None, force: bool = False,
                            peak_only: bool = False) -> None:
    """collect (2026-09-26): don't post, append the ready card to `collect`
    (the 5 key countries go out as ONE thread). force: build the card even if
    this country's standings didn't change (the thread carries all 5)."""
    """Every song of the album on one country's iTunes chart (standard +
    new edition), with +/- vs the last posted card (first one: vs the previous
    snapshot) and the peak. Posted when the album's standings changed, at most
    once per ITUNES_ALBUM_CARD_GAP_MINUTES (the chart is live), and only once a
    new song of the album is on it."""
    import generate_snapshot_images as snap
    from collectors.comp.tables_image import build_table_html, render_html_to_png
    from collectors.twitter.albums import album_emoji
    from collectors.twitter.links import amcharts_url

    album_name, album_keys = snap._resolve_album_filter(album)
    cycles = _itunes_region_cycles(today, region)
    if not cycles:
        return
    scraped_at, rows = cycles[-1]
    signature, best = _album_signature(rows, album_keys)
    new_tracks = [t for t in active_tracks if t["album"] == album]
    if not any(_row_matches(t, r) for t in new_tracks for r in best.values()):
        return
    state_key = f"album_snapshot|{song_name_key(album_name)}|itunes_{region}"
    known = state.get(state_key) or {}
    if known.get("ranks") == signature and not force:
        return
    if known.get("ranks"):
        prev, prev_at = known["ranks"], known.get("scraped_at")
    else:
        older = cycles[:-1] or _itunes_region_cycles(
            (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d"), region)
        prev, prev_at = (_album_signature(older[-1][1], album_keys)[0], older[-1][0]) if older else ({}, None)
    # What changed vs the card's baseline (owner 2026-09-27): the trigger is the
    # new songs moving among themselves; the thread is ordered by THREAD_ORDER:
    # that, then a new song at #1 / at a peak (set once the card's Peak column
    # is known, below), re-entries, songs moving up, drops, nothing.
    new_cur = _new_song_ranks(new_tracks, _signature_rows(signature))
    new_prev = _new_song_ranks(new_tracks, _signature_rows(prev))
    reorder = new_songs_order_change(new_cur, new_prev)
    if reorder:
        change = (THREAD_ORDER["reorder"], f"new songs reordered: {reorder}")
    elif any(song not in prev for song in signature):
        change = (THREAD_ORDER["reentry"], "re-entry")
    elif any(rank < prev[song] for song, rank in signature.items()):
        change = (THREAD_ORDER["up"], "song(s) up")
    elif any(prev.get(song) != rank for song, rank in signature.items()) or set(prev) - set(signature):
        change = (THREAD_ORDER["down"], "song(s) down")
    else:
        change = (THREAD_ORDER["none"], "no change")
    if NEW_ORDER_ONLY and not reorder and not force and not peak_only:
        print(f"[new_release_progression] {album_name} [iTunes {region.upper()} album card]: {change[1]}, "
              "new songs in the same order — not posted")
        return
    posted_at = _parse_release_date(known.get("posted_at") or "")
    if not force and posted_at and (now - posted_at).total_seconds() / 60.0 < ITUNES_ALBUM_CARD_GAP_MINUTES:
        print(f"[new_release_progression] {album_name} [iTunes {region.upper()} album card]: changed, "
              f"last post < {ITUNES_ALBUM_CARD_GAP_MINUTES:g} min ago — not posted")
        return
    lock_path = LOCKS_DIR / f"{_safe_slug('album_card', album_name, 'itunes', region, scraped_at)}.lock"
    if lock_path.exists():
        return

    peaks, history_start = _itunes_history_peaks(region, album_keys, scraped_at, today)
    start_label = datetime.strptime(history_start, "%Y-%m-%d").strftime("%b ") + str(int(history_start[8:]))
    entries = []
    for song, r in sorted(best.items(), key=lambda kv: _rank_of(kv[1])):
        rank = _rank_of(r)
        is_new_song = any(_row_matches(t, r) for t in new_tracks)
        before = prev.get(song)
        if before is None:
            chg_text, chg_css = ("NEW", "chg-new") if is_new_song and song not in peaks else ("RE", "chg-re")
        else:
            chg_text, chg_css = snap._rank_change(rank, before, False)
        prior = peaks.get(song)
        peak = min(rank, prior) if prior is not None else rank
        badge, since = "", ""
        if not is_new_song:
            # Out before our iTunes history (owner 2026-09-26): "PEAK TODAY",
            # best rank in this country over today's cycles.
            peak = min([rank] + [r for _at, day_rows in cycles
                                 for s2, r in _album_signature(day_rows, album_keys)[0].items() if s2 == song])
            since = "PEAK TODAY"
        elif prior is not None and rank < prior:
            badge = "NEW PEAK"
        elif prior is not None and rank == prior and before is not None and before > rank:
            badge = "RE-PEAK"
        artist = str(r.get("artist_name") or "Taylor Swift")
        if "taylor swift" not in artist.casefold():
            artist = "Taylor Swift"  # iTunes JP: "テイラー・スウィフト" (brand names stay English)
        entries.append({
            "rank": rank, "chg_text": chg_text, "chg_css": chg_css, "song": song,
            "artist": artist,
            "album": display_title_for_album(album_name),
            "image_url": _album_cover_url(album_name, str(r.get("image_url") or "")),
            "peak": peak, "peak_badge": badge, "peak_since": since, "new_song": is_new_song,
        })
    # A new song at a peak (the card's NEW PEAK / RE-PEAK badge) outranks plain
    # moves in the thread, #1 first (owner 2026-09-27: France "aurait dû être le
    # deuxième post puisque RE PEAK à #1", it came after the US' plain climbs).
    peak_entries = [e for e in entries if e["new_song"] and e["peak_badge"] in ("NEW PEAK", "RE-PEAK")]
    if peak_only and not peak_entries and not force:
        print(f"[new_release_progression] {album_name} [iTunes {region.upper()} album card]: {change[1]}, "
              f"no NEW PEAK / RE-PEAK (day {ITUNES_PEAK_ONLY_FROM_DAY}+ rule) — not posted")
        return
    if not reorder and peak_entries:
        at_one = [e for e in peak_entries if e["rank"] == 1]
        change = ((THREAD_ORDER["number_one"], f'#1: {at_one[0]["song"]}') if at_one else
                  (THREAD_ORDER["peak"], "peak: " + ", ".join(f'{e["song"]} #{e["rank"]}' for e in peak_entries)))

    display = display_title_for_album(album_name)
    market = MARKET_NAMES.get(region, country_label(region))  # full name, never "DE"
    prefix = _flag_emoji(region) or album_emoji(album_name)
    top_run = _top_run(signature.values())
    if top_run >= 3:
        lead = f'{prefix} | "{display}" songs hold the top {top_run} on iTunes in {market} right now:'
    else:
        lead = f'{prefix} | "{display}" songs on the iTunes chart in {market} right now:'
    url = amcharts_url("itunes")
    tweet = f"{lead}\n\n{url}"
    moves = new_songs_moves(new_cur, new_prev) if reorder else []
    if moves:
        # Say what moved, with the card's own Peak badge (owner 2026-09-27:
        # "Babylon a surpassed Pink Clouding et a eu un peak, on devrait le dire").
        entry_of: dict[str, dict] = {}
        for e in entries:  # best rank first: the edition the card shows
            for t in new_tracks:
                if _row_matches(t, {"song_name": e["song"]}):
                    entry_of.setdefault(t["title"], e)
        sentences = [
            _move_sentence(m, entry_of.get(m["title"], {}).get("peak_badge", ""), "" if i else f"on iTunes in {market}",
                           debut=entry_of.get(m["title"], {}).get("chg_text") == "NEW")
            for i, m in enumerate(moves)
        ]
        standing = (f'"{display}" songs hold the top {top_run} right now:' if top_run >= 3
                    else f'"{display}" songs on the chart right now:')
        # Any card can open the thread (biggest change first): room for its header.
        header = itunes_thread_header(display)
        tweet = reorder_caption(prefix=prefix, sentences=sentences, standing=standing, url=url,
                                budget=TEXT_BUDGET - _tweet_weight(header) - 2)
    elif peak_entries:
        # No reorder but a peak: say it too ("Patient Zero" is back at #1 in France).
        sentences = [_peak_sentence(e, "" if i else f"on iTunes in {market}") for i, e in enumerate(peak_entries)]
        standing = (f'"{display}" songs hold the top {top_run} right now:' if top_run >= 3
                    else f'"{display}" songs on the chart right now:')
        tweet = reorder_caption(prefix=prefix, sentences=sentences, standing=standing, url=url,
                                budget=TEXT_BUDGET - _tweet_weight(itunes_thread_header(display)) - 2)
    print(f"[new_release_progression] {display} [iTunes {region.upper()} album card]: "
          f"{len(entries)} song(s), top run {top_run}, {change[1]}")
    print(f"[new_release_progression] TWEET ({len(tweet)} chars):\n{tweet}")
    entry = {"region": region, "tweet": tweet, "image_path": None, "lock_path": lock_path, "change": change,
             "state_key": state_key, "state_value": {"ranks": signature, "scraped_at": scraped_at,
                                                     "posted_at": now.isoformat()}}
    if args.dry_run:
        if collect is not None:
            collect.append(entry)
        return

    label = snap._format_snapshot_time(scraped_at)
    vs = snap._format_snapshot_time(prev_at) if prev_at else ""
    if vs and prev_at[:10] == scraped_at[:10]:
        vs = vs.split(" · ", 1)[-1]  # same day: time only, keeps the subtitle on one line
    subtitle = f"iTunes Top Songs · {country_label(region)} · {label}" + (f" · vs {vs}" if vs else "")
    logo = f'<img class="hdr-logo" src="{_file_data_uri(HERE / "itunes_logo.svg", "image/svg+xml")}" />'
    # OUT row (owner 2026-09-27): songs of the card's baseline no longer on the chart.
    still = {song_name_key(s) for s in signature}
    dropped = [s for s, _r in sorted(prev.items(), key=lambda kv: kv[1] or 9999) if song_name_key(s) not in still]
    html_doc = build_table_html(
        title=display, subtitle=subtitle,
        col_heads=[("Pos", False), ("+/-", False), ("Track", False), ("Peak", True)],
        grid_cols="52px 64px minmax(240px,1fr) 150px", extra_css=snap.PEAK_CSS + (snap.OUT_CSS if dropped else ""),
        rows_html=snap._rows_html(entries, with_peak=True) + snap._out_row_html(dropped),
        handle=snap.HANDLE, date_str=label,
        headers_dir=HERE, header_background=ITUNES_HEADER_BG, logo_svg=logo,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = _safe_slug("album_card", album_name, "itunes", region, scraped_at)
    image_path = OUT_DIR / f"{slug}.png"
    render_html_to_png(html_doc, image_path, OUT_DIR / f"_{slug}_tmp.html")
    entry["image_path"] = image_path
    if collect is not None:
        collect.append(entry)
        return
    if args.no_post or not writes:
        print(f"[new_release_progression] --no-post: card {image_path}")
        return
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    if post_with_retries(tweet, image_path, lock_path):
        lock_path.write_text(datetime.utcnow().isoformat(), encoding="utf-8")
        state[state_key] = {"ranks": signature, "scraped_at": scraped_at, "posted_at": now.isoformat()}
        save_state(state, "itunes")
    else:
        alert(f"iTunes {region.upper()} album card post failed ({display}) — will retry next cycle.", priority="default")

def _post_itunes_album_thread(album: str, args, today: str, state: dict, active_tracks: list[dict],
                              now: datetime, writes: bool) -> None:
    """The album card of the big stores as ONE thread (owner 2026-09-26:
    "on poste le tableau de itunes pour les 5 mais dans un thread"; the 11
    big stores since 2026-09-28). Posted when the new songs moved among
    themselves in at least one ITUNES_THREAD_TRIGGER_REGIONS country (owner
    2026-09-27; same gap rule as the lone card); the thread then carries every
    ITUNES_ALBUM_CARD_REGIONS store where the album charts, the biggest change
    first (new songs reordered, then re-entries, songs up, drops, unchanged —
    market order on a tie)."""
    changed: list[dict] = []
    if itunes_peak_only(active_tracks, album, now):
        # Day 4+ (ITUNES_PEAK_ONLY_FROM_DAY): only the stores with a peak, any big store can trigger.
        for region in ITUNES_ALBUM_CARD_REGIONS:
            _post_itunes_album_card(album, args, today, state, active_tracks, now, writes, region,
                                    collect=changed, peak_only=True)
        if not changed:
            return
        cards = list(changed)
    else:
        for region in ITUNES_THREAD_TRIGGER_REGIONS:
            _post_itunes_album_card(album, args, today, state, active_tracks, now, writes, region, collect=changed)
        if not changed:
            return
        cards = list(changed)
        done = {c["region"] for c in cards}
        for region in ITUNES_ALBUM_CARD_REGIONS:
            if region not in done:
                _post_itunes_album_card(album, args, today, state, active_tracks, now, writes, region,
                                        collect=cards, force=True)
    order = {c: i for i, c in enumerate(ITUNES_ALBUM_CARD_REGIONS)}
    cards.sort(key=lambda c: (c["change"][0], order.get(c["region"], 99)))
    display = display_title_for_album(album)
    header = itunes_thread_header(display)
    thread = [(f"{header}\n\n{cards[0]['tweet']}", cards[0]["image_path"])] + [(c["tweet"], c["image_path"]) for c in cards[1:]]
    print(f"[new_release_progression] [iTunes album thread] {len(thread)} post(s): "
          + ", ".join(f"{c['region'].upper()} ({c['change'][1]})" for c in cards)
          + f" (triggered by: {', '.join(c['region'].upper() for c in changed)})")
    if args.dry_run or args.no_post or not writes:
        return
    scraped = max(c["state_value"]["scraped_at"] for c in cards)
    lock_path = LOCKS_DIR / f"{_safe_slug('itunes_album_thread', album, scraped)}.lock"
    if lock_path.exists():
        return
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    ok = post_with_retries(thread[0][0], thread[0][1], lock_path, thread=thread if len(thread) > 1 else None)
    if not ok:
        alert(f"iTunes album thread failed ({display}) — will retry next cycle.", priority="default")
        return
    stamp = datetime.utcnow().isoformat()
    lock_path.write_text(stamp, encoding="utf-8")
    for c in cards:
        c["lock_path"].write_text(stamp, encoding="utf-8")
        state[c["state_key"]] = c["state_value"]
    save_state(state, "itunes")


if __name__ == "__main__":
    main()
