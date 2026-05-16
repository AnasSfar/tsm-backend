#!/usr/bin/env python3
"""
Scrape Spotify Global directement depuis la page charts, puis filtre Taylor Swift.
Genere :
- ts_all_songs.csv
- tweet.txt

Usage :
    python filter.py YYYY-MM-DD
    python filter.py --all
    python filter.py --relog
    python filter.py --help
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
import requests as _requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parents[4]))
from core.fmt import fmt_delta, fmt_streams, fmt_streams_delta
from core.history import load, parse_date, save, update, calculate_total_days, calculate_streak
from core.logger import Logger
from core.data_paths import legacy_spotify_chart_dir, spotify_chart_dir

TS_NAME = "Taylor Swift"
CHART_ID = "regional-global-daily"
ROOT   = Path(__file__).parent
_TOOLS = Path(__file__).parent.parent           # = global/tools/
_DATA  = _TOOLS.parent / "history"              # = global/history/
SESSION_FILE = _TOOLS / "json" / "spotify_session.json"
TS_HISTORY_PATH  = _TOOLS / "json" / "ts_history.json"
TOTAL_DAYS_PATH  = _TOOLS / "json" / "total_days.json"
ARCHIVE_CSV = Path(__file__).resolve().parents[6] / "db" / "charts_history_global.csv"

if ARCHIVE_CSV.name != "charts_history_global.csv":
    raise RuntimeError(f"Invalid ARCHIVE_CSV target for Global: {ARCHIVE_CSV}")

PAGE_TIMEOUT_MS = 60_000
MAX_SCROLL_STABLE = 3
SCROLL_WAIT_MS = 500
POST_GOTO_WAIT_MS = 4000
ROW_THRESHOLD_WARN = 195

_API_BASE    = "https://charts-spotify-com-service.spotify.com/auth/v0/charts"
_UA          = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")
_BEARER_CACHE = _TOOLS / "json" / "bearer_cache.json"
_TOKEN_TTL    = 50 * 60  # 50 minutes (conservateur)


# ── API helpers ──────────────────────────────────────────────────────────────

def _load_cached_token() -> str | None:
    """Retourne le token en cache s'il est encore valide, sinon None."""
    try:
        if not _BEARER_CACHE.exists():
            return None
        data = json.loads(_BEARER_CACHE.read_text(encoding="utf-8-sig"))
        if time.time() - data.get("ts", 0) < _TOKEN_TTL:
            return data.get("token") or None
    except Exception:
        pass
    return None


def _save_cached_token(token: str) -> None:
    try:
        _BEARER_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _BEARER_CACHE.write_text(
            json.dumps({"token": token, "ts": time.time()}), encoding="utf-8"
        )
    except Exception:
        pass


def _get_bearer_token() -> str:
    """Retourne le Bearer token (depuis le cache si valide, sinon via Playwright)."""
    cached = _load_cached_token()
    if cached:
        print("  Token Bearer depuis le cache.")
        return cached

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
        page.goto("https://charts.spotify.com/charts/view/regional-global-daily/latest",
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
    _save_cached_token(token_holder[0])
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
            "streak":        clean_int(ced.get("consecutiveAppearancesOnChart")),
            "image_url":     (meta.get("displayImageUri") or "").replace("spotify:image:", "https://i.scdn.co/image/") or None,
        })
    rows.sort(key=lambda r: r["rank"])
    return rows


def _fetch_via_api(chart_date: str) -> tuple[list[dict], str] | None:
    """
    Tente de récupérer le chart via l'API interne Spotify pour la date exacte demandée.
    Retourne (rows, chart_date) ou None si l'API échoue.

    Note : le fallback "latest" a été retiré intentionnellement — "latest" peut pointer
    vers un autre jour que chart_date, ce qui stockerait des données du mauvais jour
    sous la mauvaise date. Le fallback Playwright (appelant) valide lui-même la date.
    """
    try:
        token = _get_bearer_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Referer": "https://charts.spotify.com/",
            "User-Agent": _UA,
        }
        url  = f"{_API_BASE}/{CHART_ID}/{chart_date}"
        resp = _requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            rows = _parse_api_entries(data)
            if rows:
                print(f"  API OK — {len(rows)} lignes pour {chart_date}")
                return rows, chart_date
        print(f"  API : date {chart_date} indisponible (status {resp.status_code}), fallback Playwright…")
        return None
    except Exception as e:
        print(f"  API échec ({e}), fallback Playwright…")
        return None


def print_help() -> None:
    print(
        """
Usage:
    python filter.py YYYY-MM-DD
        Scrape une date précise.

    python filter.py --all
        Scrape toutes les dates présentes dans l'arborescence qui ne sont pas encore traitées.

    python filter.py --relog
        Regénère log/tweet pour toutes les dates déjà présentes.

    python filter.py --help
        Affiche cette aide.
        """.strip()
    )


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def get_out_dir(chart_date: str) -> Path:
    return spotify_chart_dir("global", chart_date)


def get_existing_out_dir(chart_date: str) -> Path:
    new_dir = get_out_dir(chart_date)
    old_dir = legacy_spotify_chart_dir("global", chart_date)
    return new_dir if new_dir.exists() else old_dir


def chart_already_processed(chart_date: str) -> bool:
    out_dir = get_out_dir(chart_date)
    return (
        (out_dir / "ts_all_songs.csv").exists()
        or (out_dir / "no_ts.lock").exists()
    )


def get_songs_present_yesterday(chart_date, ts_history):
    yesterday = str(parse_date(chart_date) - timedelta(days=1))
    csv_path = get_existing_out_dir(yesterday) / "ts_all_songs.csv"
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
        data = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
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


def extract_date_from_url(url: str) -> str | None:
    m = re.search(r"/(\d{4}-\d{2}-\d{2})(?:[/?#]|$)", url or "")
    return m.group(1) if m else None


def try_extract_chart_date_from_page(page) -> str | None:
    patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b[A-Z][a-z]+ \d{1,2}, \d{4}\b",
    ]

    try:
        body_text = (page.locator("body").inner_text(timeout=5000) or "").strip()
    except Exception:
        body_text = ""

    for pattern in patterns:
        m = re.search(pattern, body_text)
        if not m:
            continue
        value = m.group(0)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return value
        try:
            return datetime.strptime(value, "%B %d, %Y").strftime("%Y-%m-%d")
        except Exception:
            pass

    return extract_date_from_url(page.url)


def save_debug_files(chart_date: str, page, body_text: str, suffix: str = "") -> None:
    debug_dir = get_out_dir(chart_date)
    debug_dir.mkdir(parents=True, exist_ok=True)

    suffix = f"_{suffix}" if suffix else ""

    try:
        (debug_dir / f"debug_page{suffix}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass

    try:
        (debug_dir / f"debug_body{suffix}.txt").write_text(body_text or "", encoding="utf-8")
    except Exception:
        pass

    try:
        info = {
            "final_url": page.url,
            "page_title": page.title(),
            "detected_chart_date": try_extract_chart_date_from_page(page),
        }
        (debug_dir / f"debug_meta{suffix}.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def wait_for_chart_content(page, min_rows: int = 50, max_wait_ms: int = 20_000) -> tuple[str, list[dict]]:
    """
    Attend que le body contienne suffisamment de lignes interprétables.
    Retourne (body_text, rows).
    """
    deadline = time.time() + (max_wait_ms / 1000)
    last_body = ""
    last_rows: list[dict] = []

    while time.time() < deadline:
        try:
            body_text = (page.locator("body").inner_text(timeout=5000) or "").strip()
        except Exception:
            body_text = ""

        rows = parse_chart_text(body_text)
        last_body = body_text
        last_rows = rows

        if len(rows) >= min_rows:
            return body_text, rows

        page.wait_for_timeout(1000)

    return last_body, last_rows


def scroll_until_stable(page) -> None:
    prev_height = 0
    stable_count = 0

    while stable_count < MAX_SCROLL_STABLE:
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


def extract_image_urls(page, rows: list[dict]) -> None:
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


def extract_total_days_for_ts_rows(page, rows: list[dict], chart_date: str) -> None:
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
            cached_td = json.loads(TOTAL_DAYS_PATH.read_text(encoding="utf-8-sig"))
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


def open_chart_and_parse(page, requested_date: str, route_value: str) -> tuple[list[dict], str, str | None]:
    url = f"https://charts.spotify.com/charts/view/{CHART_ID}/{route_value}"
    print(f"  Ouverture {url} ...")

    page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    # Wait for actual chart content instead of fixed delay
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

    scroll_until_stable(page)

    body_text, rows = wait_for_chart_content(page, min_rows=50, max_wait_ms=20_000)
    detected_date = try_extract_chart_date_from_page(page)

    print(f"  URL finale      : {page.url}")
    print(f"  Date detectee   : {detected_date or 'N/A'}")
    print(f"  Lignes parsees  : {len(rows)}")

    if rows:
        all_ranks = [r["rank"] for r in rows]
        print(
            f"  Rangs: {all_ranks[:5]}..."
            f"{all_ranks[-5:] if len(all_ranks) > 5 else ''}"
        )
        print("  Apercu :", rows[:3])

    if len(rows) < ROW_THRESHOLD_WARN:
        save_debug_files(requested_date, page, body_text, suffix=f"{route_value}_partial")
        print(f"  DEBUG: {len(rows)} lignes seulement — fichiers debug sauvegardes")

    if not rows:
        save_debug_files(requested_date, page, body_text, suffix=f"{route_value}_empty")
        raise RuntimeError(
            f"Aucune ligne détectée pour route '{route_value}' "
            f"(url finale: {page.url}, date détectée: {detected_date or 'N/A'})"
        )

    if route_value == "latest":
        if not detected_date:
            save_debug_files(requested_date, page, body_text, suffix="latest_no_date")
            raise RuntimeError("Impossible de détecter la date du chart 'latest'")

        print(f"  Latest détecté  : {detected_date}")

        if chart_already_processed(detected_date):
            save_debug_files(requested_date, page, body_text, suffix="latest_already_done")
            raise RuntimeError(f"Chart latest ({detected_date}) déjà traité → skip")

        if detected_date != requested_date:
            save_debug_files(requested_date, page, body_text, suffix="latest_wrong_date")
            raise RuntimeError(
                f"Chart latest pointe vers {detected_date} (attendu {requested_date}) → pas encore publié"
            )

    return rows, body_text, detected_date


def scrape_chart_rows(chart_date: str) -> tuple[list[dict], str]:
    """
    Récupère le chart Spotify.
    Tente d'abord l'API interne (rapide, pas de browser actif).
    Fallback sur Playwright + parsing HTML si l'API échoue.
    Retourne (rows, actual_chart_date).
    """
    if not SESSION_FILE.exists():
        raise RuntimeError("spotify_session.json introuvable")

    api_result = _fetch_via_api(chart_date)
    if api_result is not None:
        return api_result

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
                page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)
                page.set_default_timeout(PAGE_TIMEOUT_MS)

                rows = None
                detected_date = None

                try:
                    rows, body_text, detected_date = open_chart_and_parse(page, chart_date, chart_date)
                except Exception as first_err:
                    print(f"  Route datée échouée: {first_err}")
                    print("  Fallback vers 'latest' ...")
                    rows, body_text, detected_date = open_chart_and_parse(page, chart_date, "latest")

                if not rows:
                    raise RuntimeError("Aucune donnée exploitable après fallback")

                actual_chart_date = detected_date or chart_date

                if actual_chart_date != chart_date:
                    raise RuntimeError(
                        f"Le chart récupéré correspond à {actual_chart_date} au lieu de {chart_date}"
                    )

                if chart_already_processed(actual_chart_date):
                    raise RuntimeError(f"Chart {actual_chart_date} déjà traité → skip")

                extract_image_urls(page, rows)
                extract_total_days_for_ts_rows(page, rows, actual_chart_date)

                return rows, actual_chart_date

        except PlaywrightTimeoutError as e:
            last_error = e
            print(f"  Timeout attempt {attempt}/3 : {e}")
            time.sleep(5)

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


def _fmt_song_line(row, chart_date, ts_history) -> str:
    track = str(row["track_name"])
    dg = fmt_delta(row["rank"], row.get("previous_rank"), row.get("peak_rank"), row.get("total_days"))
    s = fmt_streams(row.get("streams"))
    sd = fmt_streams_delta(track, row.get("streams"), chart_date, ts_history)

    line = f"#{int(row['rank'])} ({dg}) {track} | {s}"
    if sd:
        line += f" ({sd})"
    return line


def write_log(log, ts_df, chart_date, ts_history):
    log.log(f"Taylor Swift on {chart_date}")
    log.log("")
    log.log("Spotify Global :")

    present_today = set(ts_df["track_name"].astype(str).tolist())
    dropped_out = get_songs_present_yesterday(chart_date, ts_history) - present_today

    for _, row in ts_df.sort_values("rank").iterrows():
        log.log(_fmt_song_line(row, chart_date, ts_history))

    for track in sorted(dropped_out):
        yesterday = str(parse_date(chart_date) - timedelta(days=1))
        entry = ts_history.get(track, {}).get(yesterday, {})
        log.log(f"(OUT) {track} | last position #{entry.get('rank', '?')}")


def _fmt_date(chart_date: str) -> str:
    d = datetime.strptime(chart_date, "%Y-%m-%d")
    return d.strftime("%A, %B %d %Y")


def generate_tweet(ts_df, chart_date, ts_history) -> str:
    header = f"📈 | Taylor Swift on Daily Global 🌍 Spotify charts ({_fmt_date(chart_date)}) :"
    lines = [header, ""]

    present_today = set(ts_df["track_name"].astype(str).tolist())
    dropped_out = get_songs_present_yesterday(chart_date, ts_history) - present_today

    for _, row in ts_df.sort_values("rank").iterrows():
        lines.append(_fmt_song_line(row, chart_date, ts_history))

    for track in sorted(dropped_out):
        yesterday = str(parse_date(chart_date) - timedelta(days=1))
        entry = ts_history.get(track, {}).get(yesterday, {})
        lines.append(f"(OUT) {track} | last position #{entry.get('rank', '?')}")

    full = "\n".join(lines)
    if len(full) <= 280:
        return full
    return header


def process_one(requested_date: str, ts_history, *, force: bool = False):
    t_total = time.time()
    print(f"Scrape du chart global pour {requested_date} ...")

    t0 = time.time()
    rows, actual_chart_date = scrape_chart_rows(requested_date)
    print(f"  [scraping] {time.time() - t0:.1f}s — {len(rows)} lignes")

    if not rows:
        raise RuntimeError(f"Aucune ligne scrapee pour {requested_date}")

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(f"Aucune donnée exploitable pour {requested_date}")

    if not df["artist_names"].astype(str).str.contains(TS_NAME, case=False, na=False).any():
        out_dir = get_out_dir(actual_chart_date)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "no_ts.lock").touch()
        print(f"{actual_chart_date} - aucune chanson TS.")
        return

    t0 = time.time()
    ts_df = df[df["artist_names"].astype(str).str.contains(TS_NAME, case=False, na=False)].copy()

    for _, row in ts_df.iterrows():
        update(
            ts_history,
            str(row["track_name"]),
            actual_chart_date,
            int(row["rank"]),
            row.get("streams"),
            previous_rank=row.get("previous_rank"),
            peak_rank=row.get("peak_rank"),
        )
    print(f"  [filtrage TS] {time.time() - t0:.1f}s — {len(ts_df)} chansons")

    # Calculer DAYS (total unique) et STREAK (consécutif)
    if not ts_df.empty:
        total_days_list = []
        streak_list = []
        for _, row in ts_df.iterrows():
            track = str(row.get("track_name", ""))
            td = calculate_total_days(ts_history, track, actual_chart_date)
            st = calculate_streak(ts_history, track, actual_chart_date)
            total_days_list.append(td)
            streak_list.append(st)
        ts_df["total_days"] = total_days_list
        ts_df["streak"] = streak_list

    t0 = time.time()
    out_dir = get_out_dir(actual_chart_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_df.to_csv(out_dir / "ts_all_songs.csv", index=False)
    update_total_days_file(ts_df, actual_chart_date)

    ts_rows_json = [
        {
            k: (
                None
                if (hasattr(v, "__class__") and v.__class__.__name__ == "float" and str(v) == "nan")
                else v
            )
            for k, v in r.items()
        }
        for r in ts_df.to_dict(orient="records")
    ]
    (out_dir / f"ts_chart_{actual_chart_date}.json").write_text(
        json.dumps(ts_rows_json, ensure_ascii=False),
        encoding="utf-8",
    )

    log = Logger()
    write_log(log, ts_df, actual_chart_date, ts_history)

    tweet = generate_tweet(ts_df, actual_chart_date, ts_history)
    (out_dir / "tweet.txt").write_text(tweet, encoding="utf-8")
    print(f"  [écriture fichiers] {time.time() - t0:.1f}s")

    t0 = time.time()
    append_to_archive_csv(actual_chart_date, ts_df, force=force)
    print(f"  [archive CSV] {time.time() - t0:.1f}s")

    print(f"OK {actual_chart_date} -> {out_dir}/")
    print(f"  [TOTAL] {time.time() - t_total:.1f}s")


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


def _remove_date_from_archive_csv(chart_date: str) -> None:
    """Supprime toutes les lignes de chart_date dans charts_history_global.csv."""
    if not ARCHIVE_CSV.exists():
        return
    import csv as _csv
    FIELDNAMES = ["date", "song_name", "rank", "streams", "previous_rank", "peak_rank", "total_days", "streak", "movement"]
    with ARCHIVE_CSV.open("r", newline="", encoding="utf-8-sig") as f:
        kept = [r for r in _csv.DictReader(f) if (r.get("date") or "").strip() != chart_date]
    with ARCHIVE_CSV.open("w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        w.writerows(kept)
    print(f"  Archive CSV: {chart_date} supprimé ({len(kept)} lignes restantes)")


def append_to_archive_csv(chart_date: str, ts_df, *, force: bool = False) -> None:
    """Appende les données TS du jour dans db/charts_history_global.csv si absentes.

    Si force=True, supprime d'abord l'entrée existante pour chart_date.
    """
    if ts_df is None or ts_df.empty:
        return
    if force:
        _remove_date_from_archive_csv(chart_date)
    elif _date_in_archive_csv(chart_date):
        return
    import csv as _csv

    def _v(val):
        if val is None:
            return ""
        if isinstance(val, float) and str(val) == "nan":
            return ""
        return val

    _EXPECTED_HEADER = ["date", "song_name", "rank", "streams", "previous_rank", "peak_rank", "total_days", "streak", "movement"]

    # Migrate existing CSV if it's missing the "streak" column
    if ARCHIVE_CSV.exists():
        with ARCHIVE_CSV.open("r", encoding="utf-8", newline="") as f:
            reader = _csv.reader(f)
            existing_header = next(reader, [])
        if "streak" not in existing_header:
            rows_to_migrate = []
            with ARCHIVE_CSV.open("r", encoding="utf-8", newline="") as f:
                dr = _csv.DictReader(f)
                for r in dr:
                    r["streak"] = r.get("total_days", "")
                    rows_to_migrate.append(r)
            with ARCHIVE_CSV.open("w", newline="", encoding="utf-8") as f:
                w = _csv.writer(f)
                w.writerow(_EXPECTED_HEADER)
                for r in rows_to_migrate:
                    w.writerow([r.get(col, "") for col in _EXPECTED_HEADER])

    write_header = not ARCHIVE_CSV.exists()
    with ARCHIVE_CSV.open("a", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        if write_header:
            w.writerow(_EXPECTED_HEADER)
        for _, row in ts_df.iterrows():
            w.writerow(
                [
                    chart_date,
                    _v(row.get("track_name")),
                    _v(row.get("rank")),
                    _v(row.get("streams")),
                    _v(row.get("previous_rank")),
                    _v(row.get("peak_rank")),
                    _v(row.get("total_days")),
                    _v(row.get("streak")),
                    _v(row.get("movement")),
                ]
            )
    print(f"  Archive CSV mise à jour pour {chart_date} ({len(ts_df)} chansons)")


def rebuild_from_ts_csvs(root: Path) -> dict:
    history = {}
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


def main():
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print_help()
        return

    run_all = "--all" in args
    run_relog = "--relog" in args
    run_reexport = "--reexport" in args
    force = "--force" in args
    non_flag_args = [a for a in args if not a.startswith("--")]
    target = None if (run_all or run_relog) else (non_flag_args[0] if non_flag_args else None)

    h = load(TS_HISTORY_PATH)

    if run_relog:
        for year_dir in sorted(_DATA.iterdir()):
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
                    log = Logger()
                    write_log(log, ts_df, day_dir.name, h)
                    tweet = generate_tweet(ts_df, day_dir.name, h)
                    (day_dir / "tweet.txt").write_text(tweet, encoding="utf-8")
                    print(f"OK {day_dir.name} regenere")
        return

    if run_all:
        dates = []
        for year_dir in sorted(_DATA.iterdir()):
            if not year_dir.is_dir() or not re.match(r"^\d{4}$", year_dir.name):
                continue
            for month_dir in sorted(year_dir.iterdir()):
                if not month_dir.is_dir():
                    continue
                for day_dir in sorted(month_dir.iterdir()):
                    if day_dir.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", day_dir.name):
                        dates.append(day_dir.name)

        for d in sorted(dates):
            out_dir = get_out_dir(d)
            if (out_dir / "ts_all_songs.csv").exists() or (out_dir / "no_ts.lock").exists():
                continue
            try:
                process_one(d, h)
            except Exception as e:
                print(f"X {d} - {e}")

        history = rebuild_from_ts_csvs(_DATA)
        save(history, TS_HISTORY_PATH)
        print(f"ts_history rebuilt - {len(history)} chansons")
        return

    if not target:
        raise RuntimeError("Donne une date: python filter.py YYYY-MM-DD")

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target):
        raise RuntimeError("Date invalide, format attendu: YYYY-MM-DD")

    if run_reexport:
        # Reexporte depuis ts_all_songs.csv existant sans re-scraper Spotify
        csv_path = get_out_dir(target) / "ts_all_songs.csv"
        if not csv_path.exists():
            raise RuntimeError(f"ts_all_songs.csv introuvable pour {target}: {csv_path}")
        ts_df = pd.read_csv(csv_path)
        if ts_df.empty:
            raise RuntimeError(f"ts_all_songs.csv vide pour {target}")
        append_to_archive_csv(target, ts_df, force=True)
        print(f"Reexport OK {target} -> {len(ts_df)} chansons dans charts_history_global.csv")
        return

    process_one(target, h, force=force)
    save(h, TS_HISTORY_PATH)
    print(f"ts_history updated - {len(h)} chansons")


if __name__ == "__main__":
    main()
