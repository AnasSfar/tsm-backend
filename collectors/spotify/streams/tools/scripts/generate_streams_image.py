#!/usr/bin/env python3
"""
generate_streams_image.py — génère le PNG des chansons les plus streamées daily (top configurable, défaut=10).

Lit  : db/streams_history.csv  +  db/discography/songs.json  +  db/discography/covers.json
Ecrit: data/YYYY/MM/YYYY-MM-DD/update_streams/streams_image*.png

Usage:
  python generate_streams_image.py               # dernière date dans le CSV
  python generate_streams_image.py 2026-03-15    # date spécifique
"""
import concurrent.futures
import csv
import json
import re
import sys
from datetime import date as date_cls, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR   = Path(__file__).resolve().parent          # streams/tools/scripts/
_TOOLS       = SCRIPT_DIR.parent                        # streams/tools/
ROOT         = SCRIPT_DIR.parents[1]                    # streams/
REPO_ROOT    = SCRIPT_DIR.parents[4]                    # repo root
DB_DIR       = REPO_ROOT / "db"

sys.path.insert(0, str(ROOT.parent))         # collectors/spotify/ for core.*
sys.path.insert(0, str(ROOT.parent.parent))  # collectors/ for comp.*

from core.data_paths import first_existing_db_history, update_streams_dir  # noqa: E402
from comp.fmt import fmt_num, fmt_delta  # noqa: E402
from comp.discography import build_cover_map, build_track_album_map  # noqa: E402
from comp.tables_image import (  # noqa: E402
    download_as_data_uri, pick_header_image, get_dominant_color,
    SPOTIFY_SVG, build_table_html,
)

HISTORY_PATH = first_existing_db_history("streams_history.csv")
COVERS_PATH  = DB_DIR / "discography" / "covers.json"
DISCOGRAPHY_ROOT = DB_DIR / "discography"
HEADERS_DIR  = _TOOLS / "headers"
HANDLE       = "@swiftiescharts"
IMAGE_DATA_URI_CACHE_PATH = _TOOLS / ".image_data_uri_cache.json"

TOP_N = 15


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def rank_change(rank: int, prev_rank) -> tuple[str, str]:
    if prev_rank is None:
        return "NEW", "chg-new"
    delta = int(prev_rank) - rank
    if delta > 0:
        return f"▲{delta}", "chg-up"
    elif delta < 0:
        return f"▼{abs(delta)}", "chg-dn"
    return "=", "chg-eq"


# Compatibility aliases for generate_weekend_streams_image.py
def load_covers() -> dict:
    return build_cover_map(COVERS_PATH)


def load_track_album_map() -> dict:
    return build_track_album_map(DISCOGRAPHY_ROOT)


def _pick_header_image() -> Path | None:
    return pick_header_image(HEADERS_DIR)


def _dominant_color(img_path: Path) -> str:
    return get_dominant_color(img_path)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_song_db() -> dict:
    """Returns {track_id: {title, artist, image_url, type, single_image, song_family}} from discography JSONs."""
    import re as _re
    result = {}

    def _consume_sections(sections: list[dict], source_name: str) -> None:
        for section in sections:
            for t in section.get("tracks", []):
                url = (t.get("url") or t.get("spotify_url") or "").strip()
                m = _re.search(r"track/([A-Za-z0-9]+)", url)
                if not m:
                    continue
                track_id = m.group(1)
                if track_id in result:
                    continue
                artists = t.get("artists") or []
                result[track_id] = {
                    "title":     (t.get("title") or "").strip(),
                    "artist":    t.get("primary_artist") or (artists[0] if artists else "Taylor Swift"),
                    "image_url": (t.get("image_url") or "").strip(),
                    "type":      t.get("type", "album"),
                    "single_image": (t.get("single_image") or "").strip(),
                    "song_family": t.get("song_family", ""),
                }

    albums_dir = DISCOGRAPHY_ROOT / "albums"
    if albums_dir.exists():
        for album_file in sorted(albums_dir.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
                _consume_sections(payload.get("sections", []) if isinstance(payload, dict) else [], album_file.name)
            except Exception as e:
                print(f"Erreur {album_file.name}: {e}")

    songs_json = DISCOGRAPHY_ROOT / "songs.json"
    if songs_json.exists():
        try:
            _consume_sections(json.loads(songs_json.read_text(encoding="utf-8-sig")), "songs.json")
        except Exception as e:
            print(f"Erreur songs.json: {e}")
    return result


def load_history(target_date: str) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Returns (today_rows, yesterday_rows, last_week_rows) from streams_history.csv.
    Each row: {track_id, streams, daily_streams}
    """
    target_day = date_cls.fromisoformat(target_date)
    yesterday_day = target_day - timedelta(days=1)
    last_week_day = target_day - timedelta(days=7)
    yesterday = str(yesterday_day)
    day_before = str(target_day - timedelta(days=2))
    last_week = str(last_week_day)
    week_before = str(target_day - timedelta(days=8))
    today_rows: dict[str, dict] = {}
    yesterday_rows: dict[str, dict] = {}
    before_rows: dict[str, dict] = {}
    last_week_rows: dict[str, dict] = {}
    week_before_rows: dict[str, dict] = {}
    latest_before: dict[str, dict[str, tuple[date_cls, int]]] = {
        "today": {},
        "yesterday": {},
        "last_week": {},
    }
    checkpoints = {
        "today": target_day,
        "yesterday": yesterday_day,
        "last_week": last_week_day,
    }

    def _parse_optional_int(raw: str | None) -> int | None:
        s = (raw or "").strip()
        if not s:
            return None
        try:
            return int(s)
        except Exception:
            return None

    with open(HISTORY_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            d = row["date"]
            try:
                row_day = date_cls.fromisoformat(d)
            except Exception:
                continue
            track_id = row["track_id"]
            streams = int(row["streams"] or 0)

            for checkpoint_name, checkpoint_day in checkpoints.items():
                if row_day >= checkpoint_day:
                    continue
                existing = latest_before[checkpoint_name].get(track_id)
                if existing is None or row_day > existing[0]:
                    latest_before[checkpoint_name][track_id] = (row_day, streams)

            if d not in (target_date, yesterday, day_before, last_week, week_before):
                continue
            entry = {
                "track_id": track_id,
                "streams": streams,
                "daily_streams": _parse_optional_int(row.get("daily_streams")),
            }
            if d == target_date:
                today_rows[track_id] = entry
            elif d == yesterday:
                yesterday_rows[track_id] = entry
            elif d == day_before:
                before_rows[track_id] = entry
            elif d == last_week:
                last_week_rows[track_id] = entry
            else:
                week_before_rows[track_id] = entry

    def _fill_missing_daily(cur: dict[str, dict], prev: dict[str, dict]) -> None:
        for tid, e in cur.items():
            if e.get("daily_streams") is not None:
                continue
            p = prev.get(tid)
            if not p:
                continue
            diff = e.get("streams", 0) - p.get("streams", 0)
            if diff >= 0:
                e["daily_streams"] = diff

    _fill_missing_daily(today_rows, yesterday_rows)
    _fill_missing_daily(yesterday_rows, before_rows)
    _fill_missing_daily(last_week_rows, week_before_rows)

    def _fill_missing_daily_from_latest(cur: dict[str, dict], checkpoint_name: str) -> None:
        prior_totals = latest_before[checkpoint_name]
        for tid, e in cur.items():
            if e.get("daily_streams") is not None:
                continue
            prior = prior_totals.get(tid)
            if prior is None:
                continue
            diff = e.get("streams", 0) - prior[1]
            if diff >= 0:
                e["daily_streams"] = diff

    _fill_missing_daily_from_latest(today_rows, "today")
    _fill_missing_daily_from_latest(yesterday_rows, "yesterday")
    _fill_missing_daily_from_latest(last_week_rows, "last_week")

    return list(today_rows.values()), list(yesterday_rows.values()), list(last_week_rows.values())


def _get_song_family_single_image_map() -> dict:
    """Returns {song_family → single_image} mapping for version inheritance."""
    family_map = {}

    albums_dir = DISCOGRAPHY_ROOT / "albums"
    if albums_dir.exists():
        for album_file in sorted(albums_dir.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                for t in section.get("tracks", []):
                    song_family = t.get("song_family", "")
                    single_image = (t.get("single_image") or "").strip()
                    if song_family and single_image and str(single_image).startswith("http"):
                        family_map[song_family] = single_image

    songs_json = DISCOGRAPHY_ROOT / "songs.json"
    if songs_json.exists():
        try:
            groups = json.loads(songs_json.read_text(encoding="utf-8-sig"))
        except Exception:
            groups = []
        for group in groups:
            for t in group.get("tracks", []):
                song_family = t.get("song_family", "")
                single_image = (t.get("single_image") or "").strip()
                if song_family and single_image and str(single_image).startswith("http"):
                    family_map[song_family] = single_image

    return family_map


def get_cover_url(entry: dict, cover_map: dict, track_album_map: dict) -> str:
    """
    Returns cover URL for a stream entry (row from history CSV).

    Priority:
      - If type == "standalone" or "alternate_version":
        * single_image (from same song_family) > image_url (NEVER album cover)
      - Otherwise: image_url (Spotify track/API image) > covers.json (album fallback)
    """
    track_type = entry.get("type", "album")
    track_img = entry.get("image_url", "")
    single_img = entry.get("single_image", "")
    song_family = entry.get("song_family", "")
    title = entry.get("title", "")

    if track_type in ("standalone", "alternate_version"):
        family_map = _get_song_family_single_image_map()
        if song_family and song_family in family_map:
            family_img = family_map[song_family]
            if str(family_img).startswith("http"):
                return family_img
        if single_img and str(single_img).startswith("http"):
            return single_img
        if track_img and str(track_img).startswith("http"):
            return track_img
        return ""

    if track_img and str(track_img).startswith("http"):
        return track_img

    album_name = track_album_map.get(_norm(title), "")
    if album_name:
        cover = cover_map.get(_norm(album_name), "")
        if cover and str(cover).startswith("http"):
            return cover

    return ""


def get_latest_date() -> str:
    latest = ""
    with open(HISTORY_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["date"] > latest:
                latest = row["date"]
    if not latest:
        raise ValueError("streams_history.csv est vide")
    return latest


def _dedup_by_title(rows: list[dict], song_db: dict) -> list[dict]:
    """Deduplicate rows by normalized title, keeping the one with max daily_streams."""
    best: dict[str, dict] = {}
    for row in rows:
        tid  = row["track_id"]
        info = song_db.get(tid)
        if not info:
            continue
        title = info.get("title") or tid
        key   = _norm(title)
        existing = best.get(key)
        row_daily = row.get("daily_streams") or 0
        existing_daily = (existing or {}).get("daily_streams") or 0
        if existing is None or row_daily > existing_daily:
            best[key] = {**row, "title": title, "artist": info.get("artist", "Taylor Swift"),
                         "image_url": info.get("image_url", "")}
    return list(best.values())


def build_top_n(today_rows: list[dict], yesterday_rows: list[dict], last_week_rows: list[dict],
                song_db: dict, top_n: int, start_rank: int = 1) -> list[dict]:
    """
    Déduplique par titre, trie par daily_streams décroissant, retourne top N.
    Attache prev_rank et daily_streams_yesterday à chaque entrée.
    """
    yest_deduped = _dedup_by_title(yesterday_rows, song_db)
    yest_sorted  = sorted(yest_deduped, key=lambda r: (r.get("daily_streams") or 0), reverse=True)
    yest_rank_by_key  = {_norm(r["title"]): i + 1 for i, r in enumerate(yest_sorted)}
    yest_daily_by_key = {_norm(r["title"]): r.get("daily_streams") for r in yest_deduped}
    last_week_deduped = _dedup_by_title(last_week_rows, song_db)
    last_week_daily_by_key = {_norm(r["title"]): r.get("daily_streams") for r in last_week_deduped}

    today_deduped = _dedup_by_title(today_rows, song_db)
    ranked = sorted(today_deduped, key=lambda r: (r.get("daily_streams") or 0), reverse=True)
    start_index = max(0, start_rank - 1)
    top = ranked[start_index:start_index + top_n]

    for offset, entry in enumerate(top):
        key = _norm(entry["title"])
        entry["rank"] = start_rank + offset
        entry["daily_streams_yesterday"] = yest_daily_by_key.get(key)
        entry["daily_streams_last_week"] = last_week_daily_by_key.get(key)
        entry["prev_rank"]               = yest_rank_by_key.get(key)

    return top


# ---------------------------------------------------------------------------
# Image prefetch
# ---------------------------------------------------------------------------

def _load_image_data_uri_cache() -> dict[str, str]:
    try:
        data = json.loads(IMAGE_DATA_URI_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_image_data_uri_cache(cache: dict[str, str]) -> None:
    try:
        IMAGE_DATA_URI_CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def prefetch_images(top_rows: list[dict], cover_map: dict, track_album_map: dict) -> dict[str, str]:
    """Resolve cover URLs for all top entries and return {url: data_uri}."""
    urls = set()
    for entry in top_rows:
        cover_url = get_cover_url(entry, cover_map, track_album_map)
        if cover_url:
            urls.add(cover_url)

    cache = _load_image_data_uri_cache()
    result: dict[str, str] = {
        url: cache[url]
        for url in urls
        if isinstance(cache.get(url), str) and cache[url].startswith("data:")
    }
    missing_urls = sorted(urls - set(result))
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(download_as_data_uri, u): u for u in missing_urls}
        for fut, url in futures.items():
            data_uri = fut.result()
            if data_uri:
                result[url] = data_uri
                cache[url] = data_uri
    if missing_urls:
        _save_image_data_uri_cache(cache)
    return result


# ---------------------------------------------------------------------------
# HTML builders
# ---------------------------------------------------------------------------

def build_rows_html(top_rows: list[dict], cover_map: dict, track_album_map: dict,
                    image_cache: dict[str, str] | None = None) -> str:
    html = ""
    for i, entry in enumerate(top_rows):
        rank      = int(entry.get("rank") or i + 1)
        title     = entry["title"]
        artist    = entry["artist"]
        daily     = entry["daily_streams"]
        total     = entry["streams"]
        yest      = entry.get("daily_streams_yesterday")
        last_week = entry.get("daily_streams_last_week")

        cover_url = get_cover_url(entry, cover_map, track_album_map)
        if image_cache and cover_url:
            cover_url = image_cache.get(cover_url, cover_url)

        art_html = (
            f'<img class="art" src="{cover_url}" />'
            if cover_url
            else '<div class="art-ph"></div>'
        )

        delta_num, delta_pct, delta_cls = fmt_delta(daily, yest)
        week_delta_num, week_delta_pct, week_delta_cls = fmt_delta(daily, last_week)
        chg_text, chg_css = rank_change(rank, entry.get("prev_rank"))

        card_cls = "data-row"
        if rank == 1:
            card_cls += " row-gold"
        elif i % 2 != 0:
            card_cls += " row-odd"

        html += f"""<div class="{card_cls}">
  <div class="col-rank">#{rank}</div>
  <div class="col-chg {chg_css}">{chg_text}</div>
  <div class="col-entity">
    {art_html}
    <div class="entity-info">
      <div class="entity-name">{title}</div>
      <div class="entity-sub">{artist}</div>
    </div>
  </div>
  <div class="col-num"><strong>{fmt_num(daily)}</strong></div>
  <div class="col-num {delta_cls}">
    <div class="delta-wrap">
      <span class="delta-num">{delta_num}</span>
      {f'<span class="delta-pct">{delta_pct}</span>' if delta_pct else ''}
    </div>
  </div>
  <div class="col-num {week_delta_cls}">
    <div class="delta-wrap">
      <span class="delta-num">{week_delta_num}</span>
      {f'<span class="delta-pct">{week_delta_pct}</span>' if week_delta_pct else ''}
    </div>
  </div>
  <div class="col-num">{fmt_num(total)}</div>
</div>
"""
    return html


def build_html(top_rows: list[dict], target_date: str, cover_map: dict, track_album_map: dict,
               top_n: int,
               image_cache: dict[str, str] | None = None) -> str:
    from datetime import datetime
    date_fmt   = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    rows_html  = build_rows_html(top_rows, cover_map, track_album_map, image_cache)
    first_rank = top_rows[0].get("rank", 1) if top_rows else 1
    last_rank  = top_rows[-1].get("rank", top_n) if top_rows else top_n

    return build_table_html(
        title="Taylor Swift · Daily Streams",
        subtitle=f"Taylor Swift's #{first_rank}-{last_rank} most streamed songs · {date_fmt}",
        col_heads=[
            ("Rank", False), ("+/-", False), ("Track", False),
            ("Daily", True), ("Daily Chg", True), ("Weekly Chg", True), ("Total", True),
        ],
        grid_cols="46px 42px minmax(130px,1fr) 104px 94px 94px 92px",
        rows_html=rows_html,
        handle=HANDLE,
        date_str=date_fmt,
        headers_dir=HEADERS_DIR,
        body_width=800,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _streams_image_filename(*, top_n: int, start_rank: int) -> str:
    if start_rank == 1 and top_n == TOP_N:
        return "streams_image.png"
    return f"streams_image_{start_rank}_{start_rank + top_n - 1}.png"


def _render_html(browser, html: str, out_path: Path) -> None:
    page = browser.new_page(viewport={"width": 800, "height": 200}, device_scale_factor=2)
    try:
        page.set_content(html, wait_until="load")
        page.wait_for_timeout(300)
        page.locator("body").screenshot(path=str(out_path))
    finally:
        page.close()


def generate_thread_images(
    target_date: str | None = None,
    *,
    top_n: int | None = None,
    pages: int = 3,
) -> list[Path]:
    if target_date is None:
        target_date = get_latest_date()
    print(f"Date: {target_date}")

    top_n_final = TOP_N if top_n is None else int(top_n)
    if top_n_final <= 0:
        raise ValueError("top_n must be > 0")
    if pages <= 0:
        raise ValueError("pages must be > 0")

    song_db         = load_song_db()
    cover_map       = load_covers()
    track_album_map = load_track_album_map()

    today_rows, yesterday_rows, last_week_rows = load_history(target_date)
    if not today_rows:
        raise ValueError(f"Aucune donnée pour {target_date} dans {HISTORY_PATH}")

    batches: list[tuple[int, list[dict]]] = []
    all_rows: list[dict] = []
    for page_index in range(pages):
        start_rank = page_index * top_n_final + 1
        top_rows = build_top_n(today_rows, yesterday_rows, last_week_rows, song_db, top_n_final, start_rank=start_rank)
        end_rank = start_rank + len(top_rows) - 1
        print(f"Top {start_rank}-{end_rank} construit ({len(top_rows)} chansons)")
        for e in top_rows:
            daily_fmt = f"{e['daily_streams']:,}"
            print(f"  #{int(e.get('rank') or 0):2d} {e['title']:<40} {daily_fmt} streams/day")
        batches.append((start_rank, top_rows))
        all_rows.extend(top_rows)

    print("Téléchargement des images...")
    image_cache = prefetch_images(all_rows, cover_map, track_album_map)
    print(f"  {len(image_cache)} images téléchargées")

    out_dir = update_streams_dir(target_date)
    out_dir.mkdir(parents=True, exist_ok=True)

    out_paths: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for start_rank, top_rows in batches:
                html = build_html(top_rows, target_date, cover_map, track_album_map, top_n_final, image_cache)
                out_path = out_dir / _streams_image_filename(top_n=top_n_final, start_rank=start_rank)
                _render_html(browser, html, out_path)
                out_paths.append(out_path)
                print(f"\nImage générée : {out_path}")
        finally:
            browser.close()

    return out_paths


def generate(target_date: str | None = None, *, top_n: int | None = None, start_rank: int = 1) -> Path:
    if target_date is None:
        target_date = get_latest_date()
    print(f"Date: {target_date}")

    top_n_final = TOP_N if top_n is None else int(top_n)
    if top_n_final <= 0:
        raise ValueError("top_n must be > 0")
    start_rank = int(start_rank)
    if start_rank <= 0:
        raise ValueError("start_rank must be > 0")

    song_db         = load_song_db()
    cover_map       = load_covers()
    track_album_map = load_track_album_map()

    today_rows, yesterday_rows, last_week_rows = load_history(target_date)
    if not today_rows:
        raise ValueError(f"Aucune donnée pour {target_date} dans {HISTORY_PATH}")

    top_rows = build_top_n(today_rows, yesterday_rows, last_week_rows, song_db, top_n_final, start_rank=start_rank)
    end_rank = start_rank + len(top_rows) - 1
    print(f"Top {start_rank}-{end_rank} construit ({len(top_rows)} chansons)")
    for e in top_rows:
        daily_fmt = f"{e['daily_streams']:,}"
        print(f"  #{int(e.get('rank') or 0):2d} {e['title']:<40} {daily_fmt} streams/day")

    print("Téléchargement des images...")
    image_cache = prefetch_images(top_rows, cover_map, track_album_map)
    print(f"  {len(image_cache)} images téléchargées")

    html = build_html(top_rows, target_date, cover_map, track_album_map, top_n_final, image_cache)

    out_dir = update_streams_dir(target_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    if start_rank == 1 and top_n_final == TOP_N:
        filename = "streams_image.png"
    else:
        filename = f"streams_image_{start_rank}_{start_rank + top_n_final - 1}.png"
    out_path = out_dir / filename
    tmp_html = out_dir / "_streams_tmp.html"
    tmp_html.write_text(html, encoding="utf-8")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page(viewport={"width": 800, "height": 200}, device_scale_factor=2)
            page.goto(f"file:///{tmp_html.as_posix()}", wait_until="load")
            page.wait_for_timeout(300)
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        if tmp_html.exists():
            tmp_html.unlink()

    print(f"\nImage générée : {out_path}")
    return out_path


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    generate(date_arg)
