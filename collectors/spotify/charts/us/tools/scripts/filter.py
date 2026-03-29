#!/usr/bin/env python3
"""
Scrape Spotify France directement depuis la page charts, puis filtre Taylor Swift,
calcule le ranking Pop, appelle Last.fm / MusicBrainz.

Genere :
- ts_all_songs.csv
- ts_pop_songs.csv
- tweet.txt

Usage :
    python filter.py YYYY-MM-DD
    python filter.py --all
    python filter.py --relog
"""
import io
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import pandas as pd
import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parents[4]))
from core.fmt import fmt_delta, fmt_streams, fmt_streams_delta
from core.history import load, parse_date, save, update
from core.logger import Logger

from config import LASTFM_API_KEY

LASTFM_BASE  = "https://ws.audioscrobbler.com/2.0/"
_API_BASE    = "https://charts-spotify-com-service.spotify.com/auth/v0/charts"
_UA          = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")
MUSICBRAINZ_BASE = "https://musicbrainz.org/ws/2/"
MUSICBRAINZ_HEADERS = {
    "User-Agent": "SpotifyFRChartTracker/1.0 (chart-tracker@example.com)",
    "Accept": "application/json",
}

ROOT        = Path(__file__).parent
_TOOLS      = Path(__file__).parent.parent          # = fr/tools/
DATA_DIR    = _TOOLS.parent / "history"             # = fr/history/
SESSION_FILE  = _TOOLS / "json" / "spotify_session.json"
LOCAL_DB_FILE = _TOOLS / "json" / "songs_db.json"
TS_HISTORY_PATH     = _TOOLS / "json" / "ts_history.json"
TS_POP_HISTORY_PATH = _TOOLS / "json" / "ts_pop_history.json"
TOTAL_DAYS_PATH     = _TOOLS / "json" / "total_days.json"
ARCHIVE_CSV   = Path(__file__).resolve().parents[6] / "db" / "charts_history_fr.csv"

SLEEP_SECONDS = 0.20
TS_NAME = "Taylor Swift"
CHART_ID = "regional-us-daily"


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def get_out_dir(chart_date: str) -> Path:
    return DATA_DIR / chart_date[:4] / chart_date[5:7] / chart_date


def get_songs_present_yesterday(chart_date, ts_history):
    yesterday = str(parse_date(chart_date) - timedelta(days=1))
    csv_path = DATA_DIR / yesterday[:4] / yesterday[5:7] / yesterday / "ts_all_songs.csv"
    if csv_path.exists():
        try:
            df = pd.read_csv(csv_path)
            return set(df["track_name"].astype(str).tolist())
        except Exception:
            pass
    return {track for track, entries in ts_history.items() if yesterday in entries}


def update_total_days_file(ts_df, chart_date: str) -> None:
    path = TOTAL_DAYS_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    for _, row in ts_df.iterrows():
        track = str(row.get("track_name") or "")
        td = row.get("total_days")
        if track and td is not None and str(td) not in ("", "nan"):
            try:
                data[track] = {"days": int(float(td)), "date": chart_date}
            except (ValueError, TypeError):
                pass
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_int(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    s = re.sub(r"[,\.\s]", "", s)
    if re.fullmatch(r"-?\d+", s):
        n = int(s)
        return n if n > 0 else None
    return None


def normalize_track_name(s: str) -> str:
    s = (s or "").replace('\u2019', "'").replace('\u2018', "'")
    return re.sub(r"\s+", " ", s.strip())


def load_db():
    if LOCAL_DB_FILE.exists():
        try:
            return json.loads(LOCAL_DB_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_db(db):
    LOCAL_DB_FILE.write_text(
        json.dumps(db, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def lastfm_get(params):
    r = requests.get(LASTFM_BASE, params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    data = r.json()
    if "error" in data:
        raise RuntimeError(data.get("message"))
    return data


def get_release_date_from_musicbrainz(artist, track):
    try:
        r = requests.get(
            MUSICBRAINZ_BASE + "recording",
            params={
                "query": f'recording:"{track}" AND artist:"{artist}"',
                "limit": 5,
                "fmt": "json",
            },
            headers=MUSICBRAINZ_HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            return ""
        dates = []
        for rec in r.json().get("recordings", []):
            frd = rec.get("first-release-date", "")
            if frd and len(frd) == 10:
                dates.append(frd)
            for rel in rec.get("releases", []):
                d = rel.get("date", "")
                if d and len(d) == 10:
                    dates.append(d)
        return sorted(dates)[0] if dates else ""
    except Exception:
        return ""


def get_track_tags_album(artist, track):
    tags, album = [], None
    for method in ("track.getInfo", "track.gettoptags"):
        try:
            data = lastfm_get({
                "method": method,
                "api_key": LASTFM_API_KEY,
                "artist": artist,
                "track": track,
                "format": "json",
                "autocorrect": "1",
            })
            t = data.get("track", data)
            tags = [norm(x["name"]) for x in t.get("toptags", {}).get("tag", []) if isinstance(x, dict)]
            tags = [t for t in tags if t]
            if method == "track.getInfo":
                album = (t.get("album") or {}).get("title") or None
            if tags:
                break
        except Exception:
            pass

    if not tags:
        try:
            data = lastfm_get({
                "method": "artist.gettoptags",
                "api_key": LASTFM_API_KEY,
                "artist": artist,
                "format": "json",
                "autocorrect": "1",
            })
            tags = [norm(x["name"]) for x in data.get("toptags", {}).get("tag", []) if isinstance(x, dict)]
            tags = [t for t in tags if t]
        except Exception:
            pass

    return tags, album


def is_pop(tags):
    if not tags:
        return False
    if any("k-pop" in t or "kpop" in t for t in tags):
        return False
    return any("pop" in t for t in tags[:3])


def get_song_data(db, artist, track, fetch_release_date=True):
    key = f"{norm(artist)}|||{norm(track)}"
    if key in db:
        e = db[key]
        tags = e.get("tags", [])
        return tags, e.get("album"), e.get("release_date", ""), e.get("is_pop", is_pop(tags)), False

    time.sleep(SLEEP_SECONDS)
    tags, album = get_track_tags_album(artist, track)
    pop = is_pop(tags)
    release_date = ""

    if fetch_release_date:
        time.sleep(1.0)
        release_date = get_release_date_from_musicbrainz(artist, track)

    db[key] = {
        "artist": artist,
        "track": track,
        "tags": tags,
        "is_pop": pop,
        "album": album,
        "release_date": release_date,
    }
    return tags, album, release_date, pop, True


def get_pop_for_nonts(db, artist, track):
    key = f"{norm(artist)}|||{norm(track)}"
    if key in db:
        e = db[key]
        tags = e.get("tags", [])
        return e.get("is_pop", is_pop(tags))

    time.sleep(SLEEP_SECONDS)
    try:
        data = lastfm_get({
            "method": "track.gettoptags",
            "api_key": LASTFM_API_KEY,
            "artist": artist,
            "track": track,
            "format": "json",
            "autocorrect": "1",
        })
        tags = [norm(x["name"]) for x in data.get("toptags", {}).get("tag", []) if isinstance(x, dict)]
        tags = [t for t in tags if t]
    except Exception:
        tags = []

    pop = is_pop(tags)
    db[key] = {"artist": artist, "track": track, "tags": tags, "is_pop": pop}
    return pop


# ── API helpers ──────────────────────────────────────────────────────────────

def _get_bearer_token() -> str:
    """Ouvre Playwright, attend le premier appel à l'API interne, retourne le Bearer token."""
    token_holder: list[str] = []

    def _on_request(req):
        if _API_BASE.split("//")[1].split("/")[0] in req.url and not token_holder:
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
        page.goto(f"https://charts.spotify.com/charts/view/{CHART_ID}/latest",
                  wait_until="domcontentloaded", timeout=30_000)
        deadline = time.time() + 20
        while not token_holder and time.time() < deadline:
            page.wait_for_timeout(300)
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
        raise RuntimeError("Bearer token introuvable — vérifiez spotify_session.json")
    return token_holder[0]


def _parse_api_entries(data: dict) -> list[dict]:
    rows = []
    for entry in (data.get("entries") or []):
        ced  = entry.get("chartEntryData") or {}
        meta = entry.get("trackMetadata") or {}
        rank = clean_int(ced.get("currentRank"))
        streams = clean_int((ced.get("rankingMetric") or {}).get("value"))
        artists = meta.get("artists") or []
        artist_str = ", ".join(a.get("name", "") for a in artists if a.get("name"))
        track = meta.get("trackName") or ""
        if not track or rank is None:
            continue
        rows.append({
            "rank":          rank,
            "track_name":    track.strip(),
            "artist_names":  artist_str.strip(),
            "streams":       streams,
            "previous_rank": clean_int(ced.get("previousRank")),
            "peak_rank":     clean_int(ced.get("peakRank")),
            "total_days":    clean_int(ced.get("consecutiveAppearancesOnChart")),
            "image_url":     (meta.get("displayImageUri") or "").replace("spotify:image:", "https://i.scdn.co/image/") or None,
        })
    rows.sort(key=lambda r: r["rank"])
    return rows


def _fetch_via_api(chart_date: str) -> list[dict] | None:
    """
    Tente de récupérer le chart via l'API interne Spotify.
    Retourne rows ou None si l'API échoue.
    """
    try:
        token = _get_bearer_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Referer": "https://charts.spotify.com/",
            "User-Agent": _UA,
        }
        for route in [chart_date, "latest"]:
            url  = f"{_API_BASE}/{CHART_ID}/{route}"
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                rows = _parse_api_entries(resp.json())
                if rows:
                    print(f"  API OK — {len(rows)} lignes pour {chart_date}")
                    return rows
        return None
    except Exception as e:
        print(f"  API échec ({e}), fallback Playwright…")
        return None


# ── Parsing HTML (fallback) ──────────────────────────────────────────────────

def parse_chart_text(body_text: str) -> list[dict]:
    text = body_text.replace("\r\n", "\n").replace("\r", "\n")

    m = re.search(r"(?im)^\s*Streams\s*$", text)
    if m:
        text = text[m.end():]

    pattern = re.compile(
        r"""
        ^\s*(?P<rank>\d{1,3})\s*$\n
        ^\s*(?P<delta>.+?)\s*$\n
        ^\s*(?P<track>.+?)\s*$\n
        ^\s*(?P<artist>.+?)\s*$\n
        ^\s*(?P<peak>\d{1,3})\s+(?P<prev>\d{1,3}|[–—-])\s+(?P<streak>\d{1,4})\s+(?P<streams>\d{1,3}(?:,\d{3})+)\s*$
        """,
        re.MULTILINE | re.VERBOSE,
    )

    rows = []
    for match in pattern.finditer(text):
        rank = int(match.group("rank"))
        if not (1 <= rank <= 200):
            continue

        rows.append(
            {
                "rank": rank,
                "track_name": normalize_track_name(match.group("track")),
                "artist_names": normalize_track_name(match.group("artist")),
                "streams": clean_int(match.group("streams")),
                "previous_rank": clean_int(match.group("prev")),
                "peak_rank": clean_int(match.group("peak")),
                "streak": clean_int(match.group("streak")),
            }
        )

    final_rows = []
    seen = set()
    for row in rows:
        key = (row["rank"], row["track_name"].lower(), row["artist_names"].lower())
        if key in seen:
            continue
        seen.add(key)
        final_rows.append(row)

    final_rows.sort(key=lambda x: x["rank"])
    return final_rows


def scrape_chart_rows(chart_date: str) -> list[dict]:
    """
    Récupère le chart Spotify FR.
    Tente d'abord l'API interne (rapide, pas de browser actif).
    Fallback sur Playwright + parsing HTML si l'API échoue.
    """
    if not SESSION_FILE.exists():
        raise RuntimeError("spotify_session.json introuvable")

    api_result = _fetch_via_api(chart_date)
    if api_result is not None:
        return api_result

    print("  Fallback Playwright…")
    url = f"https://charts.spotify.com/charts/view/{CHART_ID}/{chart_date}"
    last_error = None

    for attempt in range(1, 4):
        browser = None
        context = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                    ],
                )

                context = browser.new_context(
                    storage_state=str(SESSION_FILE),
                    viewport={"width": 1600, "height": 2400},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/133.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                )

                page = context.new_page()
                page.set_default_navigation_timeout(60_000)
                page.set_default_timeout(60_000)

                print(f"  Ouverture {url} (attempt {attempt}/3)...")
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                # Wait for actual chart content instead of fixed 4s
                try:
                    page.wait_for_function(
                        "() => document.querySelectorAll('img[src*=\"i.scdn.co\"]').length > 5",
                        timeout=8000,
                    )
                except Exception:
                    pass

                current_url = page.url.lower()
                if "login" in current_url or "accounts.spotify.com" in current_url:
                    raise RuntimeError("Session Spotify expirée ou non connectée")

                body_text = (page.locator("body").inner_text() or "").strip()
                if "Log in with Spotify" in body_text:
                    raise RuntimeError("Session Spotify non valide")

                prev_height = 0
                stable_count = 0
                while stable_count < 3:
                    page.mouse.wheel(0, 3000)
                    try:
                        page.wait_for_function(
                            f"() => document.body.scrollHeight > {prev_height}",
                            timeout=600,
                        )
                    except Exception:
                        pass
                    new_height = page.evaluate("document.body.scrollHeight")
                    if new_height == prev_height:
                        stable_count += 1
                    else:
                        stable_count = 0
                    prev_height = new_height

                body_text = (page.locator("body").inner_text() or "").strip()
                rows = parse_chart_text(body_text)
                all_ranks = [r["rank"] for r in rows]
                print(f"  {len(rows)} lignes parsees (rangs: {all_ranks[:5]}...{all_ranks[-5:] if len(all_ranks) > 5 else ''})")
                if rows:
                    print("  Apercu :", rows[:3])

                if len(rows) < 195:
                    debug_dir = get_out_dir(chart_date)
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    (debug_dir / "debug_body.txt").write_text(body_text, encoding="utf-8")
                    print(f"  DEBUG: {len(rows)} lignes seulement — debug_body.txt sauvegarde")

                if not rows:
                    debug_dir = get_out_dir(chart_date)
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    (debug_dir / "debug_page.html").write_text(page.content(), encoding="utf-8")
                    (debug_dir / "debug_body.txt").write_text(body_text, encoding="utf-8")
                    raise RuntimeError("Aucune ligne détectée")

                # Extract album art URLs and map to rows by position
                # Filter to album-art prefix only (ab67616d) to avoid artist
                # profile photos (ab6761610000e5eb) which would shift positions.
                try:
                    img_els = page.locator("img[src*='i.scdn.co']").all()
                    img_urls = [el.get_attribute("src") for el in img_els]
                    img_urls = [u for u in img_urls if u and "ab67616d" in u]
                    img_urls = [
                        u.replace("ab67616d00001e02", "ab67616d0000b273")
                         .replace("ab67616d00004851", "ab67616d0000b273")
                        for u in img_urls
                    ]
                    for i, row in enumerate(rows):
                        if i < len(img_urls):
                            row["image_url"] = img_urls[i]
                    print(f"  {len(img_urls)} images extraites")
                except Exception as img_err:
                    print(f"  Extraction images ignoree: {img_err}")

                # Extract total_days on chart for TS songs by expanding each row
                for row in rows:
                    if TS_NAME.lower() not in str(row.get("artist_names", "")).lower():
                        continue
                    track = row["track_name"]
                    try:
                        el = page.get_by_text(track, exact=True).first
                        el.click(timeout=5000)
                        try:
                            page.wait_for_selector("text=Total days on chart", timeout=2000)
                        except Exception:
                            pass
                        td_label = page.get_by_text("Total days on chart", exact=True)
                        if td_label.count() > 0:
                            container_text = td_label.first.locator("xpath=..").inner_text()
                            m = re.search(r"(\d+)", container_text)
                            if m:
                                row["total_days"] = int(m.group(1))
                                print(f"  total_days {track}: {row['total_days']}")
                            else:
                                print(f"  total_days non parsé pour {track}: {container_text!r}")
                        else:
                            print(f"  'Total days on chart' non trouvé dans DOM pour {track}")
                        el.click(timeout=3000)
                        try:
                            page.wait_for_selector("text=Total days on chart", state="hidden", timeout=500)
                        except Exception:
                            pass
                    except Exception as td_err:
                        print(f"  total_days ignoré pour {track}: {td_err}")

                # Fallback: use last known total_days from cache + delta for songs where scraping failed
                if TOTAL_DAYS_PATH.exists():
                    try:
                        cached_td = json.loads(TOTAL_DAYS_PATH.read_text(encoding="utf-8"))
                        for row in rows:
                            if TS_NAME.lower() not in str(row.get("artist_names", "")).lower():
                                continue
                            if row.get("total_days") is None:
                                val = cached_td.get(str(row["track_name"]))
                                if val is not None:
                                    if isinstance(val, dict):
                                        last_days = val.get("days")
                                        as_of = val.get("date")
                                        if last_days is not None and as_of:
                                            delta = (parse_date(chart_date) - parse_date(as_of)).days
                                            estimated = last_days + max(0, delta)
                                        else:
                                            estimated = last_days
                                    else:
                                        estimated = val  # legacy int format
                                    row["total_days"] = estimated
                                    print(f"  total_days (cache+delta) {row['track_name']}: {estimated}")
                    except Exception:
                        pass

                return rows

        except Exception as e:
            last_error = e
            print(f"  Erreur attempt {attempt}/3 : {e}")
            time.sleep(5)

        finally:
            try:
                if context:
                    context.close()
            except Exception:
                pass
            try:
                if browser:
                    browser.close()
            except Exception:
                pass

    raise RuntimeError(f"Echec scrape {chart_date} : {last_error}")


def _fmt_ts_song_line(row, chart_date, ts_history) -> str:
    track = str(row["track_name"])
    dg = fmt_delta(row["rank"], row.get("previous_rank"), row.get("peak_rank"), row.get("total_days"))
    s = fmt_streams(row.get("streams"))
    sd = fmt_streams_delta(track, row.get("streams"), chart_date, ts_history)

    line = f"- #{int(row['rank'])} ({dg}) {track} | {s}"
    if sd:
        line += f" ({sd})"
    return line


def _fmt_ts_pop_line(row) -> str:
    track = str(row["track_name"])
    pop_total = row.get("pop_total_days")
    try:
        import math
        if isinstance(pop_total, float) and math.isnan(pop_total):
            pop_total = None
    except Exception:
        pass
    dp = fmt_delta(row["pop_rank"], row.get("previous_pop_rank"), total_days=pop_total)
    return f"- #{int(row['pop_rank'])} ({dp}) {track}"


def write_log(log, ts_df, ts_pop, chart_date, ts_history):
    log.log(f"Taylor Swift on {chart_date} :")
    log.log("")
    log.log("Spotify France :")

    present_today = set(ts_df["track_name"].astype(str).tolist())
    dropped_out = get_songs_present_yesterday(chart_date, ts_history) - present_today

    for _, row in ts_df.sort_values("rank").iterrows():
        log.log(_fmt_ts_song_line(row, chart_date, ts_history))

    for track in sorted(dropped_out):
        yesterday = str(parse_date(chart_date) - timedelta(days=1))
        entry = ts_history.get(track, {}).get(yesterday, {})
        log.log(f"(OUT) {track} | last position #{entry.get('rank', '?')}")

    log.log("")
    log.log("Spotify France (Pop) :")
    if ts_pop is None or ts_pop.empty:
        log.log("- Aucune chanson TS dans le Top Pop.")
    else:
        for _, row in ts_pop.sort_values("pop_rank").iterrows():
            log.log(_fmt_ts_pop_line(row))


def _fmt_date(chart_date: str) -> str:
    d = datetime.strptime(chart_date, "%Y-%m-%d")
    return d.strftime("%A, %B %d %Y")


def generate_tweet(ts_df, ts_pop, chart_date, ts_history) -> str:
    header = f"Taylor Swift on {_fmt_date(chart_date)}"
    lines = [header, "", "Spotify France :", ""]

    present_today = set(ts_df["track_name"].astype(str).tolist())
    dropped_out = get_songs_present_yesterday(chart_date, ts_history) - present_today

    for _, row in ts_df.sort_values("rank").iterrows():
        lines.append(_fmt_ts_song_line(row, chart_date, ts_history))

    for track in sorted(dropped_out):
        yesterday = str(parse_date(chart_date) - timedelta(days=1))
        entry = ts_history.get(track, {}).get(yesterday, {})
        lines.append(f"(OUT) {track} | last position #{entry.get('rank', '?')}")

    lines += ["", "Spotify France (Pop) :", ""]
    if ts_pop is None or ts_pop.empty:
        lines.append("- Aucune chanson TS dans le Top Pop.")
    else:
        for _, row in ts_pop.sort_values("pop_rank").iterrows():
            lines.append(_fmt_ts_pop_line(row))

    full = "\n".join(lines)
    if len(full) <= 280:
        return full
    return header


def process_one(chart_date: str, db, ts_history):
    print(f"Scrape du chart France pour {chart_date} ...")
    rows = scrape_chart_rows(chart_date)
    if not rows:
        raise RuntimeError(f"Aucune ligne scrapee pour {chart_date}")

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(f"Aucune donnée exploitable pour {chart_date}")

    if not df["artist_names"].astype(str).str.contains(TS_NAME, case=False, na=False).any():
        out_dir = get_out_dir(chart_date)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "no_ts.lock").touch()
        print(f"  {chart_date} - aucune chanson TS.")
        return 0

    tags_col, pop_col, album_col, rd_col, days_col = [], [], [], [], []
    new_calls = 0
    cd = parse_date(chart_date)

    for _, row in df.iterrows():
        track = str(row["track_name"])
        artist = str(row["artist_names"]).split(",")[0].strip()
        rank = int(row["rank"])
        is_ts = TS_NAME.lower() in str(row["artist_names"]).lower()

        if is_ts:
            tags, album, release_date, pop, fetched = get_song_data(db, artist, track, fetch_release_date=True)
            update(
                ts_history,
                track,
                chart_date,
                rank,
                row.get("streams"),
                previous_rank=row.get("previous_rank"),
                peak_rank=row.get("peak_rank"),
            )
            if fetched:
                new_calls += 1
                if new_calls % 10 == 0:
                    save_db(db)

            tags_col.append("; ".join(tags))
            album_col.append(album or "")
            rd_col.append(release_date or "")
            rd = parse_date(release_date)
            days_col.append((cd - rd).days if rd and cd else "")
        else:
            pop = get_pop_for_nonts(db, artist, track)
            tags_col.append("")
            album_col.append("")
            rd_col.append("")
            days_col.append("")

        pop_col.append(pop)

    df["lastfm_tags"] = tags_col
    df["pop_flag"] = pop_col
    df["album"] = album_col
    df["release_date"] = rd_col
    df["days_since_release"] = days_col

    pop_df = df[df["pop_flag"]].sort_values("rank").copy()
    pop_df.insert(0, "pop_rank", range(1, len(pop_df) + 1))

    pop_prev = pop_df[
        pop_df["previous_rank"].notna() & (pop_df["previous_rank"] > 0)
    ].sort_values("previous_rank")
    prev_map = {idx: i + 1 for i, idx in enumerate(pop_prev.index)}
    pop_df["previous_pop_rank"] = pop_df.index.map(prev_map)

    out_dir = get_out_dir(chart_date)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts_df = df[df["artist_names"].astype(str).str.contains(TS_NAME, case=False, na=False)].copy()
    ts_pop = pop_df[pop_df["artist_names"].astype(str).str.contains(TS_NAME, case=False, na=False)].copy()

    # Pop history : déterminer NEW vs RE-ENTRY
    try:
        ts_pop_history = json.loads(TS_POP_HISTORY_PATH.read_text(encoding="utf-8")) if TS_POP_HISTORY_PATH.exists() else {}
    except Exception:
        ts_pop_history = {}

    if not ts_pop.empty:
        pop_total_days_list = []
        for _, row in ts_pop.iterrows():
            track = str(row["track_name"])
            past = sum(1 for d in ts_pop_history.get(track, []) if d < chart_date)
            pop_total_days_list.append(past)
        ts_pop = ts_pop.copy()
        ts_pop["pop_total_days"] = pop_total_days_list

        # Mettre à jour l'historique pop
        for _, row in ts_pop.iterrows():
            track = str(row["track_name"])
            if track not in ts_pop_history:
                ts_pop_history[track] = []
            if chart_date not in ts_pop_history[track]:
                ts_pop_history[track].append(chart_date)
        TS_POP_HISTORY_PATH.write_text(
            json.dumps(ts_pop_history, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    ts_df.to_csv(out_dir / "ts_all_songs.csv", index=False)
    update_total_days_file(ts_df, chart_date)
    if not ts_pop.empty:
        ts_pop.to_csv(out_dir / "ts_pop_songs.csv", index=False)

    def _clean_row(r):
        return {k: (None if (isinstance(v, float) and str(v) == "nan") else v) for k, v in r.items()}

    # JSON streaming pour generate_chart_image.py
    ts_rows_json = [_clean_row(r) for r in ts_df.to_dict(orient="records")]
    (out_dir / f"ts_chart_{chart_date}.json").write_text(
        json.dumps(ts_rows_json, ensure_ascii=False), encoding="utf-8"
    )

    # JSON pop pour generate_chart_image.py
    if not ts_pop.empty:
        ts_pop_rows_json = [_clean_row(r) for r in ts_pop.to_dict(orient="records")]
        (out_dir / f"ts_pop_{chart_date}.json").write_text(
            json.dumps(ts_pop_rows_json, ensure_ascii=False), encoding="utf-8"
        )

    log = Logger()
    write_log(log, ts_df, ts_pop, chart_date, ts_history)

    tweet = generate_tweet(ts_df, ts_pop, chart_date, ts_history)
    (out_dir / "tweet.txt").write_text(tweet, encoding="utf-8")

    append_to_archive_csv(chart_date, ts_df)
    print(f"  OK {chart_date} -> {out_dir}/")
    return new_calls


def _date_in_archive_csv(chart_date: str) -> bool:
    if not ARCHIVE_CSV.exists():
        return False
    try:
        prefix = (chart_date + ",").encode("utf-8")
        with ARCHIVE_CSV.open("rb") as f:
            for line in f:
                if line.startswith(prefix):
                    return True
    except Exception:
        pass
    return False


def append_to_archive_csv(chart_date: str, ts_df) -> None:
    """Appende les données TS du jour dans db/charts_history_fr.csv si absentes."""
    if ts_df is None or ts_df.empty or _date_in_archive_csv(chart_date):
        return
    import csv as _csv

    def _v(val):
        if val is None:
            return ""
        if isinstance(val, float) and str(val) == "nan":
            return ""
        return val

    write_header = not ARCHIVE_CSV.exists()
    with ARCHIVE_CSV.open("a", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        if write_header:
            w.writerow(["date", "song_name", "rank", "streams", "previous_rank", "peak_rank", "total_days"])
        for _, row in ts_df.iterrows():
            w.writerow([
                chart_date,
                _v(row.get("track_name")),
                _v(row.get("rank")),
                _v(row.get("streams")),
                _v(row.get("previous_rank")),
                _v(row.get("peak_rank")),
                _v(row.get("total_days")),
            ])
    print(f"  Archive CSV mise à jour pour {chart_date} ({len(ts_df)} chansons)")


def seed_from_archive_csv() -> dict:
    """Charge db/charts_history_fr.csv comme base historique initiale."""
    import csv as _csv
    history = {}
    if not ARCHIVE_CSV.exists():
        print(f"  Avertissement: archive CSV introuvable ({ARCHIVE_CSV})")
        return history
    try:
        with ARCHIVE_CSV.open(newline="", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                date = (row.get("date") or "").strip()
                name = (row.get("song_name") or "").strip()
                if not date or not name:
                    continue
                try:
                    rank = int(row["rank"])
                except (ValueError, TypeError, KeyError):
                    continue
                update(
                    history, name, date, rank,
                    row.get("streams"),
                    previous_rank=row.get("previous_rank"),
                    peak_rank=row.get("peak_rank"),
                )
        print(f"  Archive CSV: {sum(len(v) for v in history.values())} entrées chargées ({len(history)} chansons)")
    except Exception as e:
        print(f"  Avertissement: impossible de lire l'archive CSV: {e}")
    return history


def rebuild_from_ts_csvs(root: Path, initial: dict = None) -> dict:
    history = {k: dict(v) for k, v in (initial or {}).items()}
    for csv_file in sorted(root.rglob("ts_all_songs.csv")):
        try:
            chart_date = csv_file.parent.name
            df = pd.read_csv(csv_file)
            for _, row in df.iterrows():
                update(
                    history,
                    str(row["track_name"]),
                    chart_date,
                    int(row["rank"]),
                    row.get("streams"),
                    previous_rank=row.get("previous_rank"),
                    peak_rank=row.get("peak_rank"),
                )
        except Exception as e:
            print(f"Ignore {csv_file}: {e}")
    return history


def discover_dates():
    dates = []
    for year_dir in sorted(DATA_DIR.iterdir()):
        if not year_dir.is_dir() or not re.match(r"^\d{4}$", year_dir.name):
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            for day_dir in sorted(month_dir.iterdir()):
                if day_dir.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", day_dir.name):
                    dates.append(day_dir.name)
    return dates


def main():
    args = sys.argv[1:]
    run_all = "--all" in args
    run_relog = "--relog" in args
    target = None if run_all or run_relog else (args[0] if args else None)

    db = load_db()
    h = load(TS_HISTORY_PATH)

    if run_relog:
        try:
            ts_pop_history = json.loads(TS_POP_HISTORY_PATH.read_text(encoding="utf-8")) if TS_POP_HISTORY_PATH.exists() else {}
        except Exception:
            ts_pop_history = {}

        for year_dir in sorted(DATA_DIR.iterdir()):
            if not year_dir.is_dir() or not re.match(r"^\d{4}$", year_dir.name):
                continue
            for month_dir in sorted(year_dir.iterdir()):
                if not month_dir.is_dir():
                    continue
                for day_dir in sorted(month_dir.iterdir()):
                    if not (day_dir.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", day_dir.name)):
                        continue
                    if not (day_dir / "ts_all_songs.csv").exists():
                        continue

                    ts_df = pd.read_csv(day_dir / "ts_all_songs.csv")
                    ts_pop = None
                    if (day_dir / "ts_pop_songs.csv").exists():
                        ts_pop = pd.read_csv(day_dir / "ts_pop_songs.csv")
                        if ts_pop is not None and not ts_pop.empty:
                            chart_date = day_dir.name
                            pop_total_days_list = []
                            for _, row in ts_pop.iterrows():
                                track = str(row["track_name"])
                                past = sum(1 for d in ts_pop_history.get(track, []) if d < chart_date)
                                pop_total_days_list.append(past)
                            ts_pop = ts_pop.copy()
                            ts_pop["pop_total_days"] = pop_total_days_list

                    log = Logger()
                    write_log(log, ts_df, ts_pop, day_dir.name, h)
                    tweet = generate_tweet(ts_df, ts_pop, day_dir.name, h)
                    (day_dir / "tweet.txt").write_text(tweet, encoding="utf-8")
                    print(f"  OK {day_dir.name} regenere")
        return

    if run_all:
        dates = discover_dates()
        to_process = [
            d for d in sorted(dates)
            if not (get_out_dir(d) / "ts_all_songs.csv").exists()
            and not (get_out_dir(d) / "no_ts.lock").exists()
        ]

        print(f"\n{len(to_process)} jours a traiter\n")
        for d in to_process:
            try:
                process_one(d, db, h)
                save_db(db)
            except Exception as e:
                print(f"  X {d} - {e}")

        history = rebuild_from_ts_csvs(DATA_DIR, initial=seed_from_archive_csv())
        save(history, TS_HISTORY_PATH)
        print(f"  ts_history rebuilt - {len(history)} chansons")
        return

    if not target:
        raise RuntimeError("Donne une date: python filter.py YYYY-MM-DD")

    process_one(target, db, h)
    save_db(db)
    history = rebuild_from_ts_csvs(DATA_DIR, initial=seed_from_archive_csv())
    save(history, TS_HISTORY_PATH)
    print(f"  ts_history rebuilt - {len(history)} chansons")


if __name__ == "__main__":
    main()