#!/usr/bin/env python3
"""
Fetch Spotify daily charts for all available countries in parallel, keep only
Taylor Swift songs, resolve track IDs, and write
website/site/data/charts_worldwide.json.

Also writes a per-date snapshot to:
collectors/spotify/charts/worldwide/history/YYYY/MM/YYYY-MM-DD/ts_worldwide_YYYY-MM-DD.json

The list of countries is discovered dynamically from the Spotify Charts API
overview endpoint (auth/v1/overview/GLOBAL) — no hardcoded country list.

Usage:
    python daily.py                   # uses today's date
    python daily.py 2026-03-28        # positional date
    python daily.py --date 2026-03-28 # named date
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import aiohttp
from playwright.sync_api import sync_playwright

# ── Paths ─────────────────────────────────────────────────────────────────────
# collectors/spotify/charts/worldwide/daily.py → parents[4] = tsm-backend/
ROOT            = Path(__file__).resolve().parents[4]
SESSION_FILE    = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "spotify_session.json"
OUTPUT_PATH     = ROOT / "website" / "site" / "data" / "charts_worldwide.json"
HISTORY_ROOT    = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "history"
TOTAL_DAYS_PATH = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "json" / "total_days.json"

WEBSITE_SONGS_PATH = ROOT / "website" / "site" / "data" / "songs.json"
DISCO_SONGS_PATH   = ROOT / "db" / "discography" / "songs.json"
DISCO_ALBUMS_DIR   = ROOT / "db" / "discography" / "albums"
MANUAL_MAP_PATH    = ROOT / "scripts" / "chart_title_to_track_id.json"

# ── Config ────────────────────────────────────────────────────────────────────
_API_BASE  = "https://charts-spotify-com-service.spotify.com/auth/v0/charts"
_UA        = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)
TS_NAME         = "Taylor Swift"
SEMAPHORE       = 10
_OVERVIEW_URL   = "https://charts-spotify-com-service.spotify.com/auth/v1/overview/GLOBAL"

# ── Text normalisation helpers (inlined from scripts/chartr2.py) ──────────────
_TRACK_ID_RE  = re.compile(r"track/([A-Za-z0-9]+)")
_PARENS_RE    = re.compile(r"\s*[\(\[].*?[\)\]]")
_FEAT_RE      = re.compile(r"\s+(feat\.|featuring|ft\.)\s+.*$", re.IGNORECASE)
_MULTISPACE   = re.compile(r"\s+")


def _normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return _MULTISPACE.sub(" ", s.lower().strip())


def _simplify_title(title: str) -> str:
    s = _normalize_text(title)
    s = _FEAT_RE.sub("", s)
    s = _PARENS_RE.sub("", s)
    for token in ("taylor's version", "taylors version", "from the vault",
                  "remix", "acoustic", "live", "version"):
        s = s.replace(token, "")
    return _MULTISPACE.sub(" ", s).strip(" -").strip()


def _possible_keys(title: str) -> set[str]:
    keys = set()
    n = _normalize_text(title)
    s = _simplify_title(title)
    if n:
        keys.add(n)
    if s:
        keys.add(s)
    s2 = s.replace("'", "").replace("\u2019", "")
    if s2:
        keys.add(s2)
    return {k for k in keys if k}


def _extract_track_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = _TRACK_ID_RE.search(url)
    return m.group(1) if m else None


def _get_track_id_from_item(item: Dict[str, Any]) -> Optional[str]:
    for key in ("track_id", "id"):
        val = item.get(key)
        if val:
            return str(val)
    for key in ("spotify_url", "url", "track_url"):
        val = item.get(key)
        if isinstance(val, str):
            tid = _extract_track_id_from_url(val)
            if tid:
                return tid
    return None


def _title_fields(item: Dict[str, Any]) -> list[str]:
    return [
        v.strip()
        for key in ("title", "name", "base_title", "title_clean", "song_family")
        if isinstance(v := item.get(key), str) and v.strip()
    ]


def _iter_website_songs() -> Iterable[Dict[str, Any]]:
    if WEBSITE_SONGS_PATH.exists():
        data = json.loads(WEBSITE_SONGS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            yield from (x for x in data if isinstance(x, dict))
        elif isinstance(data, dict) and isinstance(data.get("songs"), list):
            yield from (x for x in data["songs"] if isinstance(x, dict))


def _iter_disco_tracks() -> Iterable[Dict[str, Any]]:
    if DISCO_SONGS_PATH.exists():
        data = json.loads(DISCO_SONGS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            yield from (x for x in data if isinstance(x, dict))
    if DISCO_ALBUMS_DIR.exists():
        for album_file in sorted(DISCO_ALBUMS_DIR.glob("*.json"),
                                 key=lambda p: p.name.casefold()):
            payload = json.loads(album_file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            album_name = payload.get("album", "")
            for section in payload.get("sections", []):
                for track in (section.get("tracks") or []):
                    if isinstance(track, dict):
                        merged = {**track}
                        merged.setdefault("album", album_name)
                        yield merged


def build_track_lookup() -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for item in _iter_website_songs():
        tid = _get_track_id_from_item(item)
        if not tid:
            continue
        for field in _title_fields(item):
            for key in _possible_keys(field):
                lookup.setdefault(key, tid)
    for item in _iter_disco_tracks():
        tid = _get_track_id_from_item(item)
        if not tid:
            continue
        for field in _title_fields(item):
            for key in _possible_keys(field):
                lookup.setdefault(key, tid)
    return lookup


def build_manual_mapping() -> Dict[str, str]:
    if not MANUAL_MAP_PATH.exists():
        return {}
    data = json.loads(MANUAL_MAP_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in data.items():
        for key in _possible_keys(k):
            out[key] = str(v)
    return out


def resolve_track_id(
    song_name: str,
    manual: Dict[str, str],
    lookup: Dict[str, str],
) -> Optional[str]:
    keys = _possible_keys(song_name)
    # 1. manual override
    for key in keys:
        if key in manual:
            return manual[key]
    # 2. exact match
    for key in keys:
        if key in lookup:
            return lookup[key]
    # 3. substring inclusion
    for key in keys:
        for k, tid in lookup.items():
            if key in k or k in key:
                return tid
    # 4. fuzzy prefix
    for key in keys:
        for k, tid in lookup.items():
            if abs(len(key) - len(k)) <= 3 and key[:10] == k[:10]:
                return tid
    return None


# ── Spotify helpers ────────────────────────────────────────────────────────────

def _clean_int(value: object) -> Optional[int]:
    if value is None:
        return None

    try:
        n = int(float(str(value).strip()))
        return n if n > 0 else None
    except (ValueError, TypeError):
        return None


def _worldwide_history_path(chart_date: str) -> Path:
    d = datetime.strptime(chart_date, "%Y-%m-%d").date()
    return (
        HISTORY_ROOT
        / str(d.year)
        / f"{d.month:02d}"
        / chart_date
        / f"ts_worldwide_{chart_date}.json"
    )

def _get_bearer_token_and_regions() -> tuple[str, dict[str, str]]:
    """
    Récupère le Bearer token via Playwright et extrait la liste exhaustive des régions
    en combinant l'API overview ET le HTML de charts.spotify.com.
    """
    import requests as _requests
    from bs4 import BeautifulSoup

    token_holder: list[str] = []
    api_host = _API_BASE.split("//")[1].split("/")[0]
    html_holder: list[str] = []

    def _on_request(req: Any) -> None:
        if api_host in req.url and not token_holder:
            auth = req.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token_holder.append(auth[7:])

    p = sync_playwright().start()
    browser = None
    try:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(
            storage_state=str(SESSION_FILE),
            user_agent=_UA,
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        page.on("request", _on_request)
        page.goto(
            "https://charts.spotify.com/",
            wait_until="networkidle",
            timeout=30_000,
        )
        deadline = time.time() + 20
        while not token_holder and time.time() < deadline:
            page.wait_for_timeout(300)
        # Récupérer le HTML de la page pour extraire les régions
        html_holder.append(page.content())
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass

    if not token_holder:
        raise RuntimeError(
            "Bearer token not found — check global/tools/json/spotify_session.json"
        )
    token = token_holder[0]

    # 1. API overview
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
        "Referer":       "https://charts.spotify.com/",
        "User-Agent":    _UA,
    }
    _attempt = 0
    while True:
        _attempt += 1
        try:
            resp = _requests.get(_OVERVIEW_URL, headers=headers, timeout=15)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 30))
                print(f"[WARN] Overview 429 — retry dans {wait}s (tentative {_attempt})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except Exception as exc:
            wait = min(30 * _attempt, 300)
            print(f"[WARN] Overview erreur ({exc}) — retry dans {wait}s (tentative {_attempt})")
            time.sleep(wait)
    country_filters = resp.json().get("countryFilters") or []
    api_regions: dict[str, str] = {
        c["code"].lower(): c["readableName"]
        for c in country_filters
        if c.get("code") and c.get("readableName")
    }

    # 2. Extraction HTML exhaustive
    html = html_holder[0] if html_holder else ""
    soup = BeautifulSoup(html, "html.parser")
    region_map = {}
    # Cherche tous les <option> dans les menus déroulants (country/region)
    for select in soup.find_all("select"):
        for opt in select.find_all("option"):
            code = opt.get("value", "").lower()
            name = opt.text.strip()
            if code and name and code != "global":
                region_map[code] = name
    # Parfois, les régions sont dans un objet JS global (window.__INITIAL_STATE__)
    # On tente de parser les codes présents dans le HTML brut
    import re as _re
    for m in _re.finditer(r'"code":"([a-zA-Z0-9_-]+)","readableName":"([^"]+)"', html):
        code, name = m.group(1).lower(), m.group(2)
        if code and name:
            region_map[code] = name

    # 3. Fusionne toutes les sources (API + HTML)
    all_regions = dict(api_regions)
    for code, name in region_map.items():
        if code not in all_regions:
            all_regions[code] = name

    # 4. Garantit les régions clés toujours présentes
    REQUIRED_REGIONS = {
        "global": "Global",
        "fr": "France",
        "us": "United States",
        "gb": "United Kingdom",
        "de": "Germany",
        "au": "Australia",
        "ca": "Canada",
        "br": "Brazil",
        "mx": "Mexico",
        "es": "Spain",
        "it": "Italy",
        "nl": "Netherlands",
        "se": "Sweden",
        "no": "Norway",
        "fi": "Finland",
        "pl": "Poland",
        "at": "Austria",
        "ch": "Switzerland",
        "be": "Belgium",
        "pt": "Portugal",
        "nz": "New Zealand",
        "ie": "Ireland",
        "jp": "Japan",
        "sg": "Singapore",
        "ph": "Philippines",
        "id": "Indonesia",
        "my": "Malaysia",
        "tw": "Taiwan",
        "ar": "Argentina",
        "cl": "Chile",
        "co": "Colombia",
        "pe": "Peru",
        "za": "South Africa",
        "in": "India",
        # Additional confirmed Spotify Charts markets
        "ae": "United Arab Emirates",
        "cz": "Czech Republic",
        "dk": "Denmark",
        "ee": "Estonia",
        "hk": "Hong Kong",
        "hu": "Hungary",
        "is": "Iceland",
        "il": "Israel",
        "kr": "South Korea",
        "lt": "Lithuania",
        "lu": "Luxembourg",
        "lv": "Latvia",
        "pa": "Panama",
        "py": "Paraguay",
        "ro": "Romania",
        "sa": "Saudi Arabia",
        "sk": "Slovakia",
        "th": "Thailand",
        "tr": "Turkey",
        "uy": "Uruguay",
        "vn": "Vietnam",
        "bg": "Bulgaria",
        "bo": "Bolivia",
        "cr": "Costa Rica",
        "cy": "Cyprus",
        "do": "Dominican Republic",
        "ec": "Ecuador",
        "gt": "Guatemala",
        "hn": "Honduras",
        "ni": "Nicaragua",
        "si": "Slovenia",
        "sv": "El Salvador",
    }
    added = []
    for code, name in REQUIRED_REGIONS.items():
        if code not in all_regions:
            all_regions[code] = name
            added.append(code)
    if added:
        print(f"[INFO] Force-added {len(added)} required regions: {', '.join(added)}")

    print(f"[INFO] Discovered {len(all_regions)} regions total (API + HTML + required)")
    return token, all_regions


def _parse_ts_entries(data: dict) -> list[dict]:
    """Parse API response; keep only Taylor Swift entries; extract trackUri when present."""
    rows: list[dict] = []
    for entry in (data.get("entries") or []):
        ced  = entry.get("chartEntryData") or {}
        meta = entry.get("trackMetadata") or {}

        artists    = meta.get("artists") or []
        artist_str = ", ".join(a.get("name", "") for a in artists if a.get("name"))
        if TS_NAME.lower() not in artist_str.lower():
            continue

        rank       = _clean_int(ced.get("currentRank"))
        track_name = (meta.get("trackName") or "").strip()
        if not track_name or rank is None:
            continue

        # trackUri: "spotify:track:4cluDES4hQEUhmXj6TXkSo"
        track_uri = meta.get("trackUri") or ""
        track_id_from_uri: Optional[str] = (
            track_uri.split(":")[-1]
            if track_uri.startswith("spotify:track:") else None
        )

        rows.append({
            "rank":          rank,
            "track_name":    track_name,
            "artist_names":  artist_str,
            "streams":       _clean_int((ced.get("rankingMetric") or {}).get("value")),
            "previous_rank": _clean_int(ced.get("previousRank")),
            "peak_rank":     _clean_int(ced.get("peakRank")),
            "total_days":    _clean_int(ced.get("consecutiveAppearancesOnChart")),
            "_track_id_uri": track_id_from_uri,
        })
    return rows


async def _fetch_region(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    region: str,
    chart_date: str,
    headers: dict[str, str],
) -> tuple[str, list[dict]]:
    chart_id = "regional-global-daily" if region == "global" else f"regional-{region}-daily"
    url = f"{_API_BASE}/{chart_id}/{chart_date}"
    attempt = 0
    while True:
        attempt += 1
        async with sem:
            try:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        rows = _parse_ts_entries(data)
                        print(f"  [{region:>6}] {len(rows)} TS entries ({chart_date})")
                        return region, rows
                    if resp.status == 404:
                        # Chart absent pour cette région à cette date — donnée valide vide
                        return region, []
                    if resp.status == 429:
                        wait = int(resp.headers.get("Retry-After", 30))
                        print(f"  [{region:>6}] 429 — retry dans {wait}s (tentative {attempt})")
                        await asyncio.sleep(wait)
                        continue
                    if 400 <= resp.status < 500:
                        # Autre 4xx non récupérable
                        print(f"  [{region:>6}] HTTP {resp.status} — abandon")
                        return region, []
                    print(f"  [{region:>6}] HTTP {resp.status} — retry dans 10s (tentative {attempt})")
            except asyncio.TimeoutError:
                print(f"  [{region:>6}] timeout — retry dans 10s (tentative {attempt})")
            except Exception as exc:
                print(f"  [{region:>6}] erreur ({exc}) — retry dans 10s (tentative {attempt})")
        await asyncio.sleep(min(10 * attempt, 60))


async def _run_async(chart_date: str, token: str, regions: dict[str, str]) -> dict[str, list[dict]]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
        "Referer":       "https://charts.spotify.com/",
        "User-Agent":    _UA,
    }
    sem = asyncio.Semaphore(SEMAPHORE)
    async with aiohttp.ClientSession() as session:
        tasks = [
            _fetch_region(session, sem, region, chart_date, headers)
            for region in regions
        ]
        results = await asyncio.gather(*tasks)
    return dict(results)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch worldwide Spotify charts for Taylor Swift songs."
    )
    parser.add_argument("date_pos", nargs="?", metavar="YYYY-MM-DD")
    parser.add_argument("--date", metavar="YYYY-MM-DD")
    parser.add_argument(
        "--no-post",
        action="store_true",
        help="Accepted for compatibility; this script never posts to Twitter.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Accepted for compatibility; ignored by this script.",
    )
    args = parser.parse_args()

    raw_date = args.date or args.date_pos or str(date.today() - timedelta(days=1))
    try:
        chart_date = datetime.strptime(raw_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        print(f"[ERROR] Invalid date: {raw_date!r}")
        return 1

    if not SESSION_FILE.exists():
        print(f"[ERROR] Session file not found: {SESSION_FILE}")
        return 1

    print(f"[INFO] chart_date = {chart_date}")
    print("[INFO] Acquiring bearer token and discovering regions via Playwright…")
    token, regions = _get_bearer_token_and_regions()
    print(f"[INFO] Token acquired. {len(regions)} regions to fetch.")


    # Pré-skip des pays déjà présents pour cette date
    already_done: set[str] = set()
    existing_by_track: dict[str, list[dict]] = {}
    if OUTPUT_PATH.exists():
        try:
            with open(OUTPUT_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == chart_date and "by_track" in data:
                existing_by_track = data["by_track"]
                for entries in data["by_track"].values():
                    for entry in entries:
                        if "country" in entry:
                            already_done.add(entry["country"])
            if already_done:
                print(f"[INFO] Skipping {len(already_done)} regions already present for {chart_date}")
        except Exception as e:
            print(f"[WARN] Could not parse existing output: {e}")

    regions_to_fetch = {k: v for k, v in regions.items() if k not in already_done}
    print(f"[INFO] Fetching {len(regions_to_fetch)} regions (semaphore={SEMAPHORE})…")
    t0 = time.perf_counter()
    by_region = asyncio.run(_run_async(chart_date, token, regions_to_fetch))
    print(f"[INFO] Done in {time.perf_counter() - t0:.1f}s")

    print("[INFO] Resolving track IDs…")
    track_lookup  = build_track_lookup()
    manual_lookup = build_manual_mapping()

    by_track: dict[str, list[dict]] = {}
    unresolved: list[dict]          = []

    for region, rows in by_region.items():
        country_name = regions[region]
        for row in rows:
            track_id: Optional[str] = row.get("_track_id_uri")
            if not track_id:
                track_id = resolve_track_id(row["track_name"], manual_lookup, track_lookup)
            if not track_id:
                unresolved.append({"region": region, "track_name": row["track_name"]})
                continue
            prev_rank = row.get("previous_rank")
            rank = row["rank"]
            rank_change = (prev_rank - rank) if (prev_rank and rank) else None
            by_track.setdefault(track_id, []).append({
                "country":        region,
                "country_name":   country_name,
                "rank":           rank,
                "previous_rank":  prev_rank,
                "rank_change":    rank_change,
                "streams":        row["streams"],
                "peak_rank":      row["peak_rank"],
                "total_days":     row["total_days"],
            })

    # Merge back already-skipped entries from the previous run of the same date.
    # Without this, re-runs would silently discard countries collected in earlier runs.
    if existing_by_track and already_done:
        for track_id, old_entries in existing_by_track.items():
            kept = [e for e in old_entries if e.get("country") in already_done]
            if not kept:
                continue
            if track_id not in by_track:
                by_track[track_id] = kept
            else:
                new_countries = {e["country"] for e in by_track[track_id]}
                for entry in kept:
                    if entry["country"] not in new_countries:
                        by_track[track_id].append(entry)

    # Enrich with stream_change / stream_change_pct from previous day's snapshot
    prev_date = (datetime.strptime(chart_date, "%Y-%m-%d").date() - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_path = _worldwide_history_path(prev_date)
    prev_by_track: dict[str, list[dict]] = {}
    if prev_path.exists():
        try:
            prev_data = json.loads(prev_path.read_text(encoding="utf-8"))
            prev_by_track = prev_data.get("by_track", {})
        except Exception as exc:
            print(f"[WARN] Could not load previous day snapshot ({prev_date}): {exc}")

    # Load persistent total_days store seeded by backfill_total_days.py.
    total_days_store: dict[str, int] = {}
    if TOTAL_DAYS_PATH.exists():
        try:
            total_days_store = json.loads(TOTAL_DAYS_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[WARN] Could not load total_days store: {exc}")

    for track_id, entries in by_track.items():
        prev_entries = prev_by_track.get(track_id, [])
        prev_by_country = {e["country"]: e for e in prev_entries}
        for entry in entries:
            prev = prev_by_country.get(entry["country"])
            prev_streams = prev.get("streams") if prev else None
            curr_streams = entry.get("streams")
            if prev_streams and curr_streams and prev_streams > 0:
                stream_change = curr_streams - prev_streams
                entry["stream_change"] = stream_change
                entry["stream_change_pct"] = round(stream_change / prev_streams * 100, 2)
            else:
                entry["stream_change"] = None
                entry["stream_change_pct"] = None

            key = f"{track_id}|{entry['country']}"
            stored = total_days_store.get(key, 0)
            entry["total_days"] = stored + 1
            total_days_store[key] = stored + 1

    try:
        TOTAL_DAYS_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOTAL_DAYS_PATH.write_text(
            json.dumps(total_days_store, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[WARN] Could not save total_days store: {exc}")

    # Sort each track's country list by rank
    for entries in by_track.values():
        entries.sort(key=lambda e: (e["rank"] or 9999))

    total_appearances = sum(len(v) for v in by_track.values())
    print(f"[INFO] {len(by_track)} unique tracks, {total_appearances} country appearances")
    if unresolved:
        names = {r["track_name"] for r in unresolved}
        print(f"[WARN] {len(unresolved)} unresolved appearances ({len(names)} unique songs): "
              + ", ".join(sorted(names)[:5]) + ("…" if len(names) > 5 else ""))

    output = {"date": chart_date, "by_track": by_track}

    per_date_path = _worldwide_history_path(chart_date)
    per_date_path.parent.mkdir(parents=True, exist_ok=True)
    per_date_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[DONE] Written → {per_date_path}")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[DONE] Written latest → {OUTPUT_PATH}")
    maybe_upload_to_r2()
    return 0


def maybe_upload_to_r2() -> None:
    if os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("0", "false", "no"):
        print("[INFO] R2 upload skipped (UPLOAD_TO_R2 explicitly disabled)")
        return

    r2_script = ROOT / "scripts" / "r2.py"
    if not r2_script.exists():
        print(f"[WARN] R2 upload script missing: {r2_script}")
        return

    print("[STEP] Uploading exported data to R2")
    for attempt in range(1, 6):
        result = subprocess.run([sys.executable, str(r2_script)], check=False, cwd=str(ROOT))
        if result.returncode == 0:
            return
        wait = 30 * attempt
        print(f"[WARN] R2 upload failed (exit {result.returncode}), retry dans {wait}s (tentative {attempt}/5)")
        time.sleep(wait)
    print("[ERROR] R2 upload failed après 5 tentatives — poursuite sans upload")


if __name__ == "__main__":
    raise SystemExit(main())
