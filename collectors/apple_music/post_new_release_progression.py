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
window: it's a no-op when no catalog track's release_date falls inside
APPLE_MUSIC_DEBUT_WINDOW_HOURS.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
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

DEBUT_WINDOW_HOURS = float(os.getenv("APPLE_MUSIC_DEBUT_WINDOW_HOURS", "72"))
KEY_COUNTRIES = [
    c.strip().lower()
    for c in os.getenv("APPLE_MUSIC_DEBUT_KEY_COUNTRIES", "us,gb,fr,ca,au").split(",")
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
FRONTEND_PUBLIC = REPO_ROOT.parent / "tsm-frontend" / "frontend" / "public"

# Volume control (2026-09-24): a debut, a new #1 or a new peak (any rank) always
# posts; any other move posts at most once every MIN_GAP_HOURS per
# (track, platform), and a cycle where the song only dropped never posts
# unless APPLE_MUSIC_DEBUT_POST_DROPS=1.
MIN_GAP_HOURS = float(os.getenv("APPLE_MUSIC_DEBUT_MIN_GAP_HOURS", "3"))
PEAK_MIN_GAP_MINUTES = float(os.getenv("APPLE_MUSIC_DEBUT_PEAK_GAP_MINUTES", "15"))
POST_DROPS = os.getenv("APPLE_MUSIC_DEBUT_POST_DROPS", "0") == "1"

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
    return candidates


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


def active_debut_tracks(candidates: list[dict], now: datetime, window_hours: float) -> list[dict]:
    active = []
    for track in candidates:
        released = track.get("released_at")
        if released is None:
            continue
        if (now - released).total_seconds() / 3600.0 <= window_hours:
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


def _rank_delta_html(placement: dict) -> str:
    """Site's PlacementTables cell: "(▲ 3)" / "(▼ 2)" / "(NEW)", nothing when
    unchanged (the site shows no marker for delta 0)."""
    if placement.get("is_new"):
        return '<span class="oct-rank-delta rank-new"> (NEW)</span>'
    delta = placement.get("delta")
    if not delta:
        return ""
    cls = "rank-up" if delta > 0 else "rank-down"
    arrow = "\u25b2" if delta > 0 else "\u25bc"
    return f'<span class="oct-rank-delta {cls}"> ({arrow} {abs(delta)})</span>'


def _peak_badge_html(placement: dict) -> str:
    if placement.get("is_new"):
        return '<span class="new-peak">NEW</span>'
    if placement.get("new_peak"):
        return '<span class="new-peak">NEW PEAK</span>'
    if placement.get("re_peak"):
        return '<span class="new-peak">RE-PEAK</span>'
    return ""


# Card layout (2026-09-25): number of table columns for n rows, and the card
# width for that many columns. Tunable in one place (previews_and_sims/
# apple-music-debut-progression/card_sizes.py renders the variants).
CARD_WIDTH_BY_COLS = {1: 780, 2: 1000, 3: 1500, 4: 2000}


def _column_count(n: int) -> int:
    """Owner 2026-09-25 (picked from previews_and_sims/.../card_sizes/): cards
    readable without zooming, between a near-square portrait (59 rows -> 2 cols,
    2000x2330) and a landscape (71 rows -> 3 cols, 3000x1944) — at most ~30 rows
    per column, 1 column up to 20 rows, never more than 3 columns (168 rows ->
    3 x 56, still ~portrait instead of a very wide strip)."""
    if n <= 20:
        return 1
    if n <= 60:
        return 2
    return 3


def _split_columns(placements: list[dict]) -> list[list[dict]]:
    n = len(placements)
    count = _column_count(n)
    per = -(-n // count) if n else 1
    cols = [placements[i:i + per] for i in range(0, n, per)]
    return [c for c in cols if c]


def _placements_tables_html(placements: list[dict], first_col_header: str) -> str:
    tables = []
    for col in _split_columns(placements):
        rows = "".join(
            ('<tr class="oct-key">' if p["region"] in KEY_COUNTRIES else "<tr>")
            + f'<td class="oct-country"><span class="oct-country-cell">{_region_flag_html(p["region"])}'
            + f'{_html_escape(p["label"])}'
            + (' <span class="oct-key-star">★</span>' if p["region"] in KEY_COUNTRIES else "")
            + "</span></td>"
            f'<td class="oct-rank">#{p["rank"]}{_rank_delta_html(p)}</td>'
            f'<td class="oct-peak">#{p["peak"]}'
            f'{_peak_badge_html(p)}</td>'
            "</tr>"
            for p in col
        )
        tables.append(
            '<table class="overall-country-table"><thead><tr>'
            f'<th class="oct-country">{first_col_header}</th><th class="oct-rank">Ranking</th><th class="oct-peak">Peak</th>'
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )
    return "".join(tables), len(tables)


def _html_escape(value: object) -> str:
    import html as html_lib

    return html_lib.escape(str(value or ""))


def _build_progression_card_html(
    *, track: dict, placements: list[dict], scraped_at: str, platform: str,
) -> str:
    """Card = the site's own share image for this track (2026-09-24, owner:
    "match the frontend's actual design"): a port of the `.overall-song-block`
    rendered by pages/AppleMusic.jsx (AppleMusicOverallBlock) / pages/ITunes.jsx
    as captured by ShareImageButton (`.is-share-image-capturing`): header
    (cover + title + album), platform brand row (logo + "Apple Music" /
    "iTunes") + "vs <previous cycle time>", Chart|Country / Ranking tables
    split in columns like splitPlacementsIntoColumns, share footer with
    @swiftiescharts (owner: replaces the site's "THE TAYLOR SWIFT MUSEUM : A
    Taylor Swift fan project" line) + the Swifties Charts logo mask (/logo.png recolored to --text). CSS values are
    copied from styles/Charts.css, calendar.css, ITunes.css, RegionFlag.css
    and the default :root tokens (variables.css) — keep in sync if the site
    card changes. Deltas are "since the last update" (this script's state)."""
    conf = PLATFORMS[platform]
    img_uri = _remote_data_uri(track.get("image_url") or "") or (track.get("image_url") or "")
    accent = _cover_accent(img_uri)
    cover_html = (
        f'<img class="overall-song-cover" src="{_html_escape(img_uri)}" alt="" />' if img_uri else ""
    )
    title = _html_escape(track["title"])
    album = _html_escape(track.get("subtitle") or display_title_for_album(track.get("album") or ""))
    logo_mask = _file_data_uri(FRONTEND_PUBLIC / "logo.png", "image/png")
    if platform == "apple_music":
        brand_logo = _file_data_uri(FRONTEND_PUBLIC / "icons" / "apple-music-logo.webp", "image/webp")
    else:
        # Official iTunes logo (Wikimedia Commons ITunes_logo.svg), kept next to
        # this script — the site has no iTunes logo asset (owner 2026-09-24:
        # iTunes header must mirror the Apple Music brand row).
        brand_logo = _file_data_uri(HERE / "itunes_logo.svg", "image/svg+xml")
    prev_times = [p["prev_scraped_at"] for p in placements if p.get("prev_scraped_at")]
    compared = _format_clock(max(prev_times)) if prev_times else ""
    compared_html = f'<span class="am-group-brand-compared-to">vs {_html_escape(compared)}</span>' if compared else ""
    # Header right (owner 2026-09-24): logo + platform name, then date + hour
    # of this cycle, then ALWAYS the 4 tallies (even at 0) — "X #1", "X top
    # 10", "X top 50", "X charting" — pill style from the site's iTunes
    # .itunes-overall-stat.
    ranks = [p["rank"] for p in placements]
    tallies = [
        (sum(1 for r in ranks if r == 1), "#1", True),
        (sum(1 for r in ranks if r <= 10), "top 10", False),
        (sum(1 for r in ranks if r <= 50), "top 50", False),
        (len(ranks), "charting", False),
    ]
    pills_html = "".join(
        f'<span class="itunes-overall-stat{" itunes-overall-stat-no1" if hl and n else ""}">{n} {label}</span>'
        for n, label, hl in tallies
    )
    when_html = (
        f'<span class="am-group-brand-when">{_html_escape(_format_share_date(scraped_at))}'
        f' &middot; {_html_escape(_format_clock(scraped_at))}</span>'
    )
    side_html = (
        '<div class="am-group-section-brand"><span class="am-group-brand-row">'
        f'<img src="{brand_logo}" class="am-group-brand-logo" alt="" /><span>{_html_escape(conf["label"])}</span></span>'
        f'{when_html}{compared_html}<div class="itunes-overall-stats">{pills_html}</div></div>'
    )

    if platform == "apple_music":
        # Site order: Global first, then countries, by rank.
        ordered = sorted(placements, key=lambda p: (0 if p["region"] == "global" else 1, p["rank"], _market_order(p)))
        first_col = "Chart"
        footer_date = f'{conf["footer"]} - {_format_share_date(scraped_at)} - {_html_escape(_format_clock(scraped_at))}'
    else:
        ordered = sorted(placements, key=lambda p: (p["rank"], _market_order(p)))
        first_col = "Country"
        footer_date = f'{conf["footer"]} - {_format_share_date(scraped_at)}'

    tables_html, col_count = _placements_tables_html(ordered, first_col)
    # 4 columns (all-regions cards, 12+ rows): ~500 px per column so long names
    # + rank + peak badge never spill into the next column (2026-09-25 render).
    card_width = CARD_WIDTH_BY_COLS.get(col_count, CARD_WIDTH_BY_COLS[4])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<style>
  :root {{
    --text:#101828; --muted:#6b7280; --line:rgba(16,24,40,.09);
    --surface-3:rgba(255,255,255,.99); --accent:{accent};
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #f9fcfa; font-family: Inter, system-ui, "Segoe UI", sans-serif; color: var(--text); }}
  .overall-song-block {{
    width: {card_width}px;
    background: #f9fcfa;
    border-radius: 1.1rem;
    border: 1px solid var(--line);
    box-shadow: 0 2px 16px rgba(0,0,0,0.07);
    padding: 1.1rem 1.2rem 0.9rem 1.2rem;
    display: flex; flex-direction: column;
  }}
  .overall-song-header {{
    display: flex; align-items: center; gap: 0.75rem;
    margin-bottom: 0.9rem; padding-bottom: 0.75rem;
    border-bottom: 1px solid var(--line); width: 100%;
  }}
  .overall-song-header-link {{ display: flex; align-items: center; gap: 1rem; flex: 1; min-width: 0; }}
  .overall-song-cover {{
    width: 52px; height: 52px; border-radius: 0.6rem; object-fit: cover;
    flex-shrink: 0; box-shadow: 0 1px 6px rgba(0,0,0,0.1);
  }}
  .overall-song-meta {{ min-width: 0; }}
  .overall-song-title {{
    font-size: 1.05rem; font-weight: 700; color: var(--text); margin-bottom: 0.15em;
    line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .overall-song-artist {{ font-size: 0.92rem; color: var(--muted); line-height: 1.2; }}
  .am-group-section-brand {{ display: flex; flex-direction: column; align-items: flex-end; gap: 2px; margin-left: auto; }}
  .am-group-brand-row {{
    display: flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 800;
    text-transform: uppercase; letter-spacing: .05em; color: var(--muted); white-space: nowrap;
  }}
  .am-group-brand-logo {{ width: 18px; height: 18px; border-radius: 4px; flex-shrink: 0; }}
  .am-group-brand-compared-to {{ font-size: 9px; font-weight: 600; color: var(--muted); opacity: .75; white-space: nowrap; }}
  .am-group-brand-when {{ font-size: 11px; font-weight: 700; color: var(--text); white-space: nowrap; }}
  .itunes-overall-stats {{ display: flex; flex-wrap: nowrap; align-items: center; justify-content: flex-end; gap: 6px; margin-top: 4px; }}
  .itunes-overall-stat {{
    font-size: 11px; font-weight: 700; color: var(--text); background: #eef1ef;
    border-radius: 999px; padding: 2px 9px; white-space: nowrap;
  }}
  .itunes-overall-stat-no1 {{ color: #fff; background: var(--accent); }}
  .am-overall-table-grid {{ display: grid; grid-template-columns: repeat({col_count}, minmax(0, 1fr)); gap: 0 1.2rem; }}
  .overall-country-table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
  .overall-country-table thead tr {{ border-bottom: 1px solid var(--line); }}
  .overall-country-table th {{
    text-align: right; font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.04em; color: var(--muted); padding: 0 0.5rem 0.4rem 0.5rem;
  }}
  .overall-country-table th.oct-country {{ text-align: left; }}
  .overall-country-table td {{ padding: 0.35rem 0.5rem; vertical-align: middle; white-space: nowrap; text-align: right; }}
  .overall-country-table tbody tr:nth-child(even) {{ background: var(--surface-3); }}
  .oct-country {{ font-weight: 600; color: var(--accent); text-align: left !important; min-width: 64px; }}
  .oct-country-cell {{ display: inline-flex; align-items: center; gap: 7px; }}
  /* 5 key stores (owner 2026-09-25): tinted row + accent bar + star. */
  .overall-country-table tbody tr.oct-key {{ background: color-mix(in srgb, var(--accent) 13%, #fff); }}
  .overall-country-table tbody tr.oct-key td:first-child {{ box-shadow: inset 4px 0 0 var(--accent); }}
  .overall-country-table tbody tr.oct-key td {{ font-weight: 800; }}
  .oct-key-star {{ color: var(--accent); font-size: 0.95em; }}
  .oct-rank {{ font-weight: 700; color: var(--text); }}
  .oct-peak {{ font-weight: 700; color: var(--text); font-variant-numeric: tabular-nums; width: 150px; }}
  .new-peak {{
    margin-left: 6px; padding: 1px 7px; border-radius: 999px; background: var(--accent); color: #fff;
    font-size: 0.68rem; font-weight: 800; letter-spacing: .04em; vertical-align: 1px;
  }}
  th.oct-peak {{ color: var(--muted); }}
  .oct-rank-delta {{ font-size: 0.85em; font-weight: 400; }}
  .oct-rank-delta.rank-up {{ color: #2a9d5c; }}
  .oct-rank-delta.rank-down {{ color: #e05c3a; }}
  .oct-rank-delta.rank-new {{ color: var(--muted); font-weight: 700; }}
  .region-flag {{
    display: inline-block; width: 20px; height: 15px; flex-shrink: 0; object-fit: cover;
    border-radius: 2px; border: 1px solid rgba(16,24,40,.07);
    box-shadow: 0 1px 2px rgba(0,0,0,0.12); vertical-align: -2px;
  }}
  .region-flag-globe {{ width: 15px; height: 15px; color: var(--muted); border: none; box-shadow: none; border-radius: 0; }}
  .am-share-card-footer {{
    margin-top: 16px; padding-top: 12px; border-top: 1px solid rgba(16,24,40,.07);
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: 0.02em; text-transform: uppercase;
  }}
  .am-share-card-date {{ flex: 0 0 auto; color: var(--text); }}
  .am-share-card-brand {{ display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-width: 0; text-align: right; }}
  .brand-logo {{
    display: inline-block; background-color: var(--text);
    -webkit-mask-image: url("{logo_mask}"); mask-image: url("{logo_mask}");
    -webkit-mask-repeat: no-repeat; mask-repeat: no-repeat;
    -webkit-mask-position: center; mask-position: center;
    -webkit-mask-size: contain; mask-size: contain;
  }}
  .am-share-card-brand-logo {{ width: 26px; height: 26px; flex: 0 0 auto; }}
</style>
</head>
<body>
<div class="overall-song-block" id="card">
  <div class="overall-song-header">
    <div class="overall-song-header-link">
      {cover_html}
      <div class="overall-song-meta">
        <div class="overall-song-title">{title}</div>
        <div class="overall-song-artist">{album}</div>
      </div>
    </div>
    {side_html}
  </div>
  <div class="am-overall-table-grid">{tables_html}</div>
  <div class="am-share-card-footer">
    <div class="am-share-card-date">{footer_date}</div>
    <div class="am-share-card-brand">
      <span>@swiftiescharts</span>
      <span class="brand-logo am-share-card-brand-logo" role="img" aria-label="Swifties Charts"></span>
      <span>thetsmuseum.app</span>
    </div>
  </div>
</div>
</body>
</html>"""


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
        if prev_rank is None:
            # Never posted here, but maybe already charting before (the Encore
            # album sat on Apple Music album charts from 00:00, 2026-09-25):
            # compare with the previous real cycle, never call it NEW.
            prev_rank = _previous_cycle_rank(track, source, cache, today, scraped_at)
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

    emoji = album_emoji(track.get("album"))
    body = f'{emoji} | "{track["title"]}" {headline}{extras}.'
    world = worldwide_sentence(
        worldwide, platform, f"the {PLATFORMS[platform]['label']} albums chart" if is_album else "",
    )
    if world:
        body += f"\n\n{world}"
    url = amcharts_url(PLATFORMS[platform]["site_source"])
    tweet = f"{prefix}{body}\n\n{url}"

    def weighted(text: str) -> int:
        # X weighting: a link counts 23, an emoji 2 -> keep a margin under 280.
        # But twitter.py::_validate_tweet_lengths refuses any text over 280 RAW
        # chars (the Encore album tweet, 283 raw / ~265 for X, failed 3x on
        # 2026-09-25 15h): the stricter of the two decides.
        link = 23 - len(url) if url in text else 0
        x_weight = len(text) + link + sum(1 for ch in text if ord(ch) > 0x2000)
        return max(x_weight, len(text) - 5)

    if weighted(tweet) > 275 and prefix:
        # Thread opener (header + best song, owner 2026-09-25 example has no
        # link): the link goes first, then the extras.
        tweet = tweet.replace(f"\n\n{url}", "", 1)
    if weighted(tweet) > 275 and extras:
        tweet = tweet.replace(extras, "", 1)
    return tweet


def render_card(*, track: dict, placements: list[dict], max_scraped_at: str, platform: str) -> Path:
    html_content = _build_progression_card_html(
        track=track, placements=placements, scraped_at=max_scraped_at, platform=platform,
    )
    out_path = OUT_DIR / f"{_safe_slug(track['key'], platform, max_scraped_at)}.png"
    _render_card_png(html_content, out_path, scale=3 if len(placements) <= 24 else 2)
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
                    slot_timeout=DEBUT_POST_SLOT_TIMEOUT if debut else POST_SLOT_TIMEOUT,
                )
            else:
                ok = post_with_image(
                    tweet, image_path, TWITTER_SESSION,
                    priority=DEBUT_POST_PRIORITY if debut else POST_PRIORITY,
                    skip_if=lambda: lock_path.exists(),
                    slot_timeout=DEBUT_POST_SLOT_TIMEOUT if debut else POST_SLOT_TIMEOUT,
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
        return f'🧵 | "{display_title_for_album(next(iter(albums)))}"\'s songs on {chart}.'
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
        print(f"[new_release_progression] [{label}] No release candidate — nothing to do.")
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
        print(f"[new_release_progression] [{label}] No track inside its debut window — nothing to post.")
        return
    print(f"[new_release_progression] [{label}] {len(active_tracks)} track(s) in window: "
          + ", ".join(t["title"] for t in active_tracks))

    if writes:
        from collectors.spotify.core.twitter import post_with_image

    song_sources = [s for s in sources if s.get("kind", "song") == "song"]
    album_sources = [s for s in sources if s.get("kind") == "album"]
    items = [(t, song_sources) for t in active_tracks]
    if album_sources:  # iTunes Top Albums card (owner 2026-09-25)
        items += [(a, album_sources) for a in album_items(active_tracks)]

    # Pass 1: what is worth posting this cycle.
    to_post: list[tuple[dict, list[dict]]] = []
    idle_songs: list[tuple[dict, list[dict]]] = []  # charting, but nothing worth a post alone
    for track, item_sources in items:
        try:
            placements = _collect_track_placements(track, item_sources, cache, state, today)
            if not placements and track.get("is_album"):
                continue
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
            held = post_due(track, moved, state, now)
            if held:
                if not track.get("is_album"):
                    idle_songs.append((track, placements))
                print(f"[new_release_progression] {track['title']} [{label}]: {held} — not posted")
                continue
            to_post.append((track, placements))
        except Exception as exc:
            import traceback

            traceback.print_exc()
            alert(f'{label}: "{track["title"]}" crashed: {type(exc).__name__}: {exc}')

    # A songs thread carries EVERY song of the release (owner 2026-09-25:
    # "since it's a thread every song should go in"): the ones that triggered
    # it first, then the others with their current ranks.
    idle_keys: set[str] = set()
    if any(not t.get("is_album") for t, _ in to_post):
        to_post += idle_songs
        idle_keys = {t["key"] for t, _ in idle_songs}
    # Biggest news first: the thread opener is the best song (owner 2026-09-25).
    to_post.sort(key=lambda tp: _importance(*tp))

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
            tweet = build_tweet_text(track=track, placements=placements, platform=platform, worldwide=worldwide)
            print(f"[new_release_progression] {track['title']} [{label}]: "
                  + ("carried by the thread (no move)" if track["key"] in idle_keys
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
    groups = [[e] for e in ready if e["track"].get("is_album") or len(songs) < 2]
    if len(songs) >= 2:
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
    if platform == "apple_music":
        try:
            _post_global_snapshot(args, today, state, platform, writes)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            alert(f"Global card crashed: {type(exc).__name__}: {exc}")
        for country in POP_CARD_COUNTRIES:
            try:
                _post_pop_snapshot(country, args, today, state, active_tracks, platform, writes)
            except Exception as exc:
                import traceback

                traceback.print_exc()
                alert(f"Pop card crashed ({country}): {type(exc).__name__}: {exc}")
    if platform == "itunes":
        for album in sorted({t["album"] for t in active_tracks if t["album"]}):
            for region in ITUNES_ALBUM_CARD_REGIONS:
                try:
                    _post_itunes_album_card(album, args, today, state, active_tracks, now, writes, region)
                except Exception as exc:
                    import traceback

                    traceback.print_exc()
                    alert(f"iTunes album card crashed ({album}, {region}): {type(exc).__name__}: {exc}")
    albums = sorted({t["album"] for t in active_tracks if t["album"]}) if platform == "apple_music" else []
    for album in albums:
        try:
            _post_album_snapshot(album, args, today, state, active_tracks, platform)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            alert(f"Album card crashed ({album}): {type(exc).__name__}: {exc}")


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
    rendered = snap.generate(today, "global", None, OUT_DIR)
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
# Only a strong event of a new track triggers it: #1, entering the top 10, or a
# new peak inside the top 10. Moves further down (UK/FR at #41-#199) never do.
POP_GENRE = "Pop"
POP_CARD_COUNTRIES = [
    c.strip().lower() for c in os.getenv("APPLE_MUSIC_DEBUT_POP_COUNTRIES", ",".join(KEY_COUNTRIES)).split(",") if c.strip()
]
POP_TOP_N = 10


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


def _pop_events(active_tracks: list[dict], rows: list[dict], state: dict, country: str) -> tuple[list[dict], dict]:
    """(events, new per-track state) for one country's current Pop chart.
    State per track = {"rank", "peak"} of the last cycle seen (posted or not)."""
    events: list[dict] = []
    updates: dict[str, dict] = {}
    for track in active_tracks:
        _row, rank = _best_match(track, rows)
        key = f"pop|{song_name_key(track['title'])}|{country}"
        prev = state.get(key) or {}
        prev_rank, prev_peak = prev.get("rank"), prev.get("peak")
        if rank is None:
            # Off the chart: keep the peak, forget the rank (a comeback into
            # the top 10 is an entry again).
            if prev:
                updates[key] = {"rank": None, "peak": prev_peak}
            continue
        peak = rank if prev_peak is None else min(prev_peak, rank)
        updates[key] = {"rank": rank, "peak": peak}
        if rank > POP_TOP_N or rank == prev_rank:
            continue
        if rank == 1 and prev_rank != 1:
            kind = "number_one"
        elif prev_rank is None:
            kind = "debut"
        elif prev_rank > POP_TOP_N:
            kind = "top10_entry"
        elif prev_peak is not None and rank < prev_peak:
            kind = "new_peak"
        else:
            continue
        events.append({"track": track, "rank": rank, "kind": kind})
    events.sort(key=lambda e: ({"number_one": 0, "debut": 1, "new_peak": 2, "top10_entry": 3}[e["kind"]], e["rank"]))
    return events, updates


def _pop_event_sentence(event: dict, where: str) -> str:
    title = f'"{event["track"]["title"]}"'
    if event["kind"] == "number_one":
        return f"{title} is now #1 on the Apple Music Pop chart in {where}!"
    if event["kind"] == "new_peak":
        return f"{title} hits a new peak of #{event['rank']} on the Apple Music Pop chart in {where}."
    if event["kind"] == "debut":
        return f"{title} debuts at #{event['rank']} on the Apple Music Pop chart in {where}."
    return f"{title} enters the top 10 of the Apple Music Pop chart in {where} at #{event['rank']}."


def _post_pop_snapshot(country: str, args, today: str, state: dict, active_tracks: list[dict],
                       platform: str, writes: bool) -> None:
    import generate_snapshot_images as snap

    rows, scraped_at = snap.get_region_rows(today, country, POP_GENRE)
    if not rows or not scraped_at:
        return
    events, updates = _pop_events(active_tracks, rows, state, country)
    if not events:
        # Nothing strong: just track ranks/peaks so the next cycle compares
        # against this one.
        if writes and updates:
            state.update(updates)
            save_state(state, platform)
        return
    lock_path = LOCKS_DIR / f"{_safe_slug('pop_snapshot', country, scraped_at)}.lock"
    if lock_path.exists():
        return
    where = MARKET_NAMES.get(country, country_label(country))
    print(f"[new_release_progression] [Pop {country}] {len(events)} event(s): "
          + ", ".join(f"{e['track']['title']} {e['kind']} #{e['rank']}" for e in events))

    from collectors.twitter.albums import album_emoji
    from collectors.twitter.links import amcharts_url

    lead = events[0]
    others = [e for e in events[1:]]
    prefix = _flag_emoji(country) or album_emoji(lead["track"].get("album") or "", fallback="📈")
    lines = [f"{prefix} | {_pop_event_sentence(lead, where)}"]
    if others:
        lines.append("Also: " + ", ".join(f'"{e["track"]["title"]}" #{e["rank"]}' for e in others) + ".")
    tweet = "\n\n".join([*lines, f"Taylor Swift songs on the Apple Music Pop chart in {where} right now:",
                         amcharts_url("applemusic")])
    print(f"[new_release_progression] TWEET ({len(tweet)} chars):\n{tweet}")
    if args.dry_run:
        return
    rendered = snap.generate(today, country, POP_GENRE, OUT_DIR)
    image_path = rendered.replace(OUT_DIR / f"{_safe_slug('pop_snapshot', country, scraped_at)}.png")
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
        alert(f"Pop card post failed ({country}) — will retry next cycle.", priority="default")


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
        today, "global", None, OUT_DIR, album=(pending["album"], pending["album_keys"]),
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
ITUNES_ALBUM_CARD_REGIONS = [c.strip() for c in os.getenv("ITUNES_ALBUM_CARD_REGIONS", "us").split(",") if c.strip()]
ITUNES_ALBUM_CARD_GAP_MINUTES = float(os.getenv("ITUNES_ALBUM_CARD_GAP_MINUTES", "60"))
ITUNES_HEADER_BG = "linear-gradient(135deg,#ff5c6d 0%,#d17cad 55%,#9b5de5 100%)"


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
                            now: datetime, writes: bool, region: str = "us") -> None:
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
    if known.get("ranks") == signature:
        return
    posted_at = _parse_release_date(known.get("posted_at") or "")
    if posted_at and (now - posted_at).total_seconds() / 60.0 < ITUNES_ALBUM_CARD_GAP_MINUTES:
        print(f"[new_release_progression] {album_name} [iTunes {region.upper()} album card]: changed, "
              f"last post < {ITUNES_ALBUM_CARD_GAP_MINUTES:g} min ago — not posted")
        return
    if known.get("ranks"):
        prev, prev_at = known["ranks"], known.get("scraped_at")
    else:
        older = cycles[:-1] or _itunes_region_cycles(
            (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d"), region)
        prev, prev_at = (_album_signature(older[-1][1], album_keys)[0], older[-1][0]) if older else ({}, None)
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
            since = f"since {start_label}"  # out before our iTunes history: partial peak
        elif prior is not None and rank < prior:
            badge = "NEW PEAK"
        elif prior is not None and rank == prior and before is not None and before > rank:
            badge = "RE-PEAK"
        entries.append({
            "rank": rank, "chg_text": chg_text, "chg_css": chg_css, "song": song,
            "artist": str(r.get("artist_name") or "Taylor Swift"),
            "album": display_title_for_album(album_name),
            "image_url": _album_cover_url(album_name, str(r.get("image_url") or "")),
            "peak": peak, "peak_badge": badge, "peak_since": since,
        })

    display = display_title_for_album(album_name)
    market = MARKET_NAMES.get(region, country_label(region))  # full name, never "DE"
    prefix = _flag_emoji(region) or album_emoji(album_name)
    top_run = 0
    while top_run + 1 in signature.values():
        top_run += 1
    if top_run >= 3:
        lead = f'{prefix} | "{display}" songs hold the top {top_run} on iTunes in {market} right now:'
    else:
        lead = f'{prefix} | "{display}" songs on the iTunes chart in {market} right now:'
    tweet = f"{lead}\n\n{amcharts_url('itunes')}"
    print(f"[new_release_progression] {display} [iTunes {region.upper()} album card]: "
          f"{len(entries)} song(s), top run {top_run}")
    print(f"[new_release_progression] TWEET ({len(tweet)} chars):\n{tweet}")
    if args.dry_run:
        return

    label = snap._format_snapshot_time(scraped_at)
    vs = snap._format_snapshot_time(prev_at) if prev_at else ""
    if vs and prev_at[:10] == scraped_at[:10]:
        vs = vs.split(" · ", 1)[-1]  # same day: time only, keeps the subtitle on one line
    subtitle = f"iTunes Top Songs · {country_label(region)} · {label}" + (f" · vs {vs}" if vs else "")
    logo = f'<img class="hdr-logo" src="{_file_data_uri(HERE / "itunes_logo.svg", "image/svg+xml")}" />'
    html_doc = build_table_html(
        title=display, subtitle=subtitle,
        col_heads=[("Pos", False), ("+/-", False), ("Track", False), ("Peak", True)],
        grid_cols="52px 64px minmax(240px,1fr) 150px", extra_css=snap.PEAK_CSS,
        rows_html=snap._rows_html(entries, with_peak=True), handle=snap.HANDLE, date_str=label,
        headers_dir=HERE, header_background=ITUNES_HEADER_BG, logo_svg=logo,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = _safe_slug("album_card", album_name, "itunes", region, scraped_at)
    image_path = OUT_DIR / f"{slug}.png"
    render_html_to_png(html_doc, image_path, OUT_DIR / f"_{slug}_tmp.html")
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

if __name__ == "__main__":
    main()
