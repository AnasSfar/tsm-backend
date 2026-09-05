#!/usr/bin/env python3
"""
generate_albums_image.py — génère le PNG "Top Albums by Daily Streams".

Pour chaque album, compte toutes les éditions sauf "extras" / "extra".

Lit  : db/streams_history.csv + db/discography/albums/*.json
       db/discography/songs.json + db/discography/covers.json
Ecrit: snapshots/spotify_streams/YYYY/MM/YYYY-MM-DD/albums_image.png

Usage:
  python generate_albums_image.py               # dernière date dans le CSV
  python generate_albums_image.py 2026-03-15    # date spécifique
"""
import concurrent.futures
import csv
import json
import random
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

sys.path.insert(0, str(ROOT))                # collectors/spotify/streams/ for best_day_since
sys.path.insert(0, str(ROOT.parent))         # collectors/spotify/ for core.*
sys.path.insert(0, str(ROOT.parent.parent))  # collectors/ for comp.*

from core.data_paths import first_existing_db_history, update_streams_dir  # noqa: E402
from comp.fmt import fmt_num, fmt_delta, fmt_signed  # noqa: E402
from comp.discography import build_cover_map  # noqa: E402
from comp.tables_image import (  # noqa: E402
    download_as_data_uri, pick_header_image, get_dominant_color,
    rank_change, SPOTIFY_SVG, build_table_html, era_accent_color, dominant_color_from_data_uri,
    ledger_name_with_best_day, masthead_theme_for_date,
)
import best_day_since  # noqa: E402
import history_store  # noqa: E402

HISTORY_PATH = first_existing_db_history("streams_history.csv")
COVERS_PATH  = DB_DIR / "discography" / "covers.json"
ALBUMS_DIR   = DB_DIR / "discography" / "albums"
SONGS_JSON   = DB_DIR / "discography" / "songs.json"
MISC_JSON    = DB_DIR / "discography" / "misc.json"
HEADERS_DIR  = _TOOLS / "headers"
HANDLE       = "@swiftiescharts"
NON_ALBUM_ERA = "Non-Album"

# Regroupe OG + Taylor's Version sous la même ère.
ERA_MAP: dict[str, str] = {
    "Fearless (Taylor's Version)": "Fearless",
    "Speak Now (Taylor's Version)": "Speak Now",
    "Red (Taylor's Version)":      "Red",
    "1989 (Taylor's Version)":     "1989",
}

# Pour la cover, on préfère la TV quand elle existe.
ERA_COVER_PRIORITY: dict[str, list[str]] = {
    "Fearless":  ["Fearless (Taylor's Version)", "Fearless"],
    "Speak Now": ["Speak Now (Taylor's Version)", "Speak Now"],
    "Red":       ["Red (Taylor's Version)", "Red"],
    "1989":      ["1989 (Taylor's Version)", "1989"],
}

# Albums-specific CSS overrides applied on top of the ledger table style.
_ALBUMS_EXTRA_CSS = """
.ledger-art,.ledger-art-ph{width:38px;height:38px;border-radius:6px}
.ledger-art-spacer{width:38px;height:38px;flex-shrink:0}
.ledger-art-collage{
  width:38px;height:38px;border-radius:6px;overflow:hidden;flex-shrink:0;
  display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;
  background:var(--ledger-cols-bg);box-shadow:0 2px 8px rgba(0,0,0,.3);
}
.ledger-art-collage img{width:100%;height:100%;object-fit:cover;display:block}
.ledger-row{padding:6px 18px}
.ledger-chg{font-size:11px;font-weight:800}
.ledger-rank{font-size:22px}
.ledger-name{font-size:13.5px;line-height:1.2}
.ledger-num{font-size:12.5px}
.ledger-delta-num{font-size:12px;font-weight:700}
.ledger-delta-pct{font-size:10px}
.ledger-row-total{
  border-top:2px solid var(--ledger-row-border);
  background:var(--ledger-cols-bg);
}
.ledger-row-total .ledger-name,
.ledger-row-total .ledger-daily,
.ledger-row-total .ledger-total{font-weight:900}
"""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def _headers_dir_for_top_eras() -> Path:
    specific = HEADERS_DIR / "top_eras"
    if specific.exists() and any(p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} for p in specific.iterdir()):
        return specific
    return HEADERS_DIR


def _is_unranked_era(name: str) -> bool:
    norm = _norm(name)
    parts = set(norm.split("_"))
    if "misc" in parts or "standalone" in parts:
        return True
    return norm in {
        "miscellaneous",
        "non_album",
        "standalone_extras",
        "standalone_and_extras",
    }


def pick_non_album_collage(track_map: dict, today: dict, n: int = 4) -> list[str]:
    """
    Retourne jusqu'à n covers de chansons hors-album (misc.json) pour composer
    le collage 2x2 de l'ère "Non-Album". Priorité aux titres qui ont des
    streams sur la date courante ; fallback sur tout le catalogue hors-album.
    Sélection aléatoire à chaque génération.
    """
    def _pool(require_today: bool) -> list[str]:
        seen: set[str] = set()
        urls: list[str] = []
        for track_id, info in track_map.items():
            if info.get("album") != NON_ALBUM_ERA:
                continue
            if require_today and track_id not in today:
                continue
            url = (info.get("image_url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            urls.append(url)
        return urls

    pool = _pool(require_today=True)
    if len(pool) < n:
        for url in _pool(require_today=False):
            if url not in pool:
                pool.append(url)
    if not pool:
        return []
    return random.sample(pool, min(n, len(pool)))


# Compatibility alias: load_covers used by generate_weekend_streams_image.py
def load_covers() -> dict:
    return build_cover_map(COVERS_PATH)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_album_track_map() -> dict[str, dict]:
    """
    Returns {track_id: {album, edition, image_url}}
    Only from albums/*.json + songs.json.
    Compte toutes les éditions sauf "extras" / "extra".
    """
    result = {}

    def _as_bool(value) -> bool | None:
        if value is None:
            return None
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return None

    def _is_chart_extra(section: dict, track: dict) -> bool:
        track_flag = _as_bool(track.get("chart_extra"))
        if track_flag is not None:
            return track_flag
        section_flag = _as_bool(section.get("chart_extra"))
        return bool(section_flag) if section_flag is not None else False

    def _consume_sections(
        sections: list[dict],
        album_name_fallback: str = "",
        *,
        force_album_name: bool = False,
    ) -> None:
        for section in sections:
            album_name = album_name_fallback if force_album_name else (section.get("album") or album_name_fallback)
            for track in section.get("tracks", []):
                url = (track.get("url") or track.get("spotify_url") or "").strip()
                m = re.search(r"track/([A-Za-z0-9]+)", url)
                if not m:
                    continue
                track_id = m.group(1)
                if track_id not in result:
                    result[track_id] = {
                        "album":       album_name,
                        "image_url":   (track.get("image_url") or "").strip(),
                        "chart_extra": _is_chart_extra(section, track),
                        "on_album":    _as_bool(track.get("on_album")) is not False,
                        "release_date": (track.get("release_date") or section.get("release_date") or "").strip(),
                    }

    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
                if not isinstance(payload, dict):
                    continue
                _consume_sections(
                    payload.get("sections", []),
                    payload.get("album", ""),
                )
            except Exception as e:
                print(f"Erreur {album_file.name}: {e}")

    if SONGS_JSON.exists():
        try:
            _consume_sections(json.loads(SONGS_JSON.read_text(encoding="utf-8-sig")))
        except Exception as e:
            print(f"Erreur {SONGS_JSON.name}: {e}")

    if MISC_JSON.exists():
        try:
            _consume_sections(
                json.loads(MISC_JSON.read_text(encoding="utf-8-sig")),
                NON_ALBUM_ERA,
                force_album_name=True,
            )
        except Exception as e:
            print(f"Erreur {MISC_JSON.name}: {e}")
    return result


def load_public_total_row(target_date: str) -> dict:
    """Returns the public-site exact totals for the total row."""
    target_day = date_cls.fromisoformat(target_date)
    dates = {
        "today": target_date,
        "yest_daily": str(target_day - timedelta(days=1)),
        "week_daily": str(target_day - timedelta(days=7)),
    }

    def _totals_for(day: str) -> dict:
        path = update_streams_dir(day) / "site_history.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Missing public site history for total row: {path}. "
                "Run export_for_web before generating the Top Eras image."
            )
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid public site history payload: {path}")
        return {
            "streams": sum(int(v.get("s") or v.get("streams") or 0) for v in payload.values() if isinstance(v, dict)),
            "daily_streams": sum(int(v.get("d") or v.get("daily_streams") or 0) for v in payload.values() if isinstance(v, dict)),
        }

    today = _totals_for(dates["today"])
    yest = _totals_for(dates["yest_daily"])
    week = _totals_for(dates["week_daily"])
    return {
        "album": "Total",
        "streams": today["streams"],
        "daily_streams": today["daily_streams"],
        "yest_daily": yest["daily_streams"],
        "week_daily": week["daily_streams"],
        "cover_url": "",
        "rank": None,
        "prev_rank": None,
        "is_total": True,
    }


def load_history(target_date: str) -> tuple[dict, dict, dict]:
    """Returns today, yesterday, and same weekday last week track history maps."""
    target_day = date_cls.fromisoformat(target_date)
    yesterday = str(target_day - timedelta(days=1))
    day_before = str(target_day - timedelta(days=2))
    last_week = str(target_day - timedelta(days=7))
    last_week_prev = str(target_day - timedelta(days=8))
    today: dict[str, dict] = {}
    yest:  dict[str, dict] = {}
    before: dict[str, dict] = {}
    week: dict[str, dict] = {}
    week_before: dict[str, dict] = {}

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
            if d not in (target_date, yesterday, day_before, last_week, last_week_prev):
                continue
            entry = {
                "streams":       int(row["streams"] or 0),
                "daily_streams": _parse_optional_int(row.get("daily_streams")),
                "estimated_reason": (row.get("estimated_reason") or "").strip(),
            }
            if d == target_date:
                today[row["track_id"]] = entry
            elif d == last_week:
                week[row["track_id"]] = entry
            elif d == last_week_prev:
                week_before[row["track_id"]] = entry
            else:
                if d == yesterday:
                    yest[row["track_id"]] = entry
                else:
                    before[row["track_id"]] = entry

    def _fill_missing_daily(cur: dict[str, dict], prev: dict[str, dict]) -> None:
        for tid, e in cur.items():
            if e.get("daily_streams") is not None:
                continue
            reason = e.get("estimated_reason") or ""
            if reason == "manual_trusted" or reason.startswith("collection_incident_"):
                continue
            p = prev.get(tid)
            if not p:
                continue
            diff = e.get("streams", 0) - p.get("streams", 0)
            if diff >= 0:
                e["daily_streams"] = diff

    _fill_missing_daily(today, yest)
    _fill_missing_daily(yest, before)
    _fill_missing_daily(week, week_before)

    return today, yest, week


def get_latest_date() -> str:
    latest = ""
    with open(HISTORY_PATH, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["date"] > latest:
                latest = row["date"]
    if not latest:
        raise ValueError("streams_history.csv est vide")
    return latest


def build_album_rows(
    today: dict,
    yest: dict,
    week: dict,
    track_map: dict,
    covers: dict,
    *,
    target_date: str | None = None,
    merge_eras: bool = True,
) -> list[dict]:
    """
    Agrège les streams par album (éditions incluses seulement).
    Retourne une liste triée par daily_streams desc.
    """
    albums: dict[str, dict] = {}
    target_day = date_cls.fromisoformat(target_date) if target_date else None
    yesterday_day = target_day - timedelta(days=1) if target_day else None
    last_week_day = target_day - timedelta(days=7) if target_day else None

    def _released_by(info: dict, day: date_cls | None) -> bool:
        if day is None:
            return True
        raw = str(info.get("release_date") or "").strip()
        if not raw:
            return True
        try:
            return date_cls.fromisoformat(raw[:10]) <= day
        except ValueError:
            return True

    def _usable_daily(row: dict | None) -> int | None:
        if not row:
            return None
        value = row.get("daily_streams")
        if value is None:
            return None
        try:
            daily = int(value)
        except (TypeError, ValueError):
            return None
        if daily < 0:
            return None
        return daily

    def _add_comparison_daily(bucket: dict, key: str, row: dict | None, required: bool) -> None:
        if bucket.get(key) is None:
            return
        daily = _usable_daily(row)
        if daily is None:
            if required:
                bucket[key] = None
            return
        bucket[key] += daily

    tracks_by_album: dict[str, list[tuple[str, dict]]] = {}
    for track_id, info in track_map.items():
        album = info.get("album") or ""
        if not album:
            continue
        tracks_by_album.setdefault(album, []).append((track_id, info))

    merge_losers = history_store.pick_active_catalog_merge_losers(
        {track_id: today_row.get("streams") for track_id, today_row in today.items() if track_id in track_map},
        {track_id: track_map[track_id] for track_id in today if track_id in track_map},
    )
    if merge_losers:
        print(
            f"[albums_image] Excluding {len(merge_losers)} currently merged track(s) "
            f"from album/era aggregates: {sorted(merge_losers)}"
        )

    for album, album_tracks in tracks_by_album.items():
        cover_url = covers.get(_norm(album), "")
        if not cover_url:
            for _, info in album_tracks:
                if info.get("image_url"):
                    cover_url = info["image_url"]
                    break

        albums[album] = {
            "album":         album,
            "streams":       0,
            "daily_streams": 0,
            "yest_daily":    0,
            "week_daily":    0,
            "cover_url":     cover_url,
        }

        for track_id, info in album_tracks:
            if track_id in merge_losers:
                continue
            if not merge_eras and (info.get("chart_extra") or not info.get("on_album", True)):
                continue
            t = today.get(track_id)
            if t is None:
                continue
            y = yest.get(track_id, {})
            w = week.get(track_id, {})
            albums[album]["streams"]       += t["streams"]
            albums[album]["daily_streams"] += (t.get("daily_streams") or 0)
            strict_comparison = not info.get("chart_extra")
            _add_comparison_daily(
                albums[album],
                "yest_daily",
                y,
                strict_comparison and _released_by(info, yesterday_day),
            )
            _add_comparison_daily(
                albums[album],
                "week_daily",
                w,
                strict_comparison and _released_by(info, last_week_day),
            )

    if not merge_eras:
        yest_ranked = sorted(
            [r for r in albums.values() if r.get("yest_daily")],
            key=lambda r: r["yest_daily"],
            reverse=True,
        )
        yest_rank_by_album = {r["album"]: i + 1 for i, r in enumerate(yest_ranked)}
        rows = sorted(albums.values(), key=lambda r: r["daily_streams"], reverse=True)
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
            row["prev_rank"] = yest_rank_by_album.get(row["album"])
        _attach_non_album_collage(rows, track_map, today)
        return rows

    eras: dict[str, dict] = {}
    for album_name, album_data in albums.items():
        era_name = ERA_MAP.get(album_name, album_name)
        if era_name not in eras:
            priority = ERA_COVER_PRIORITY.get(era_name, [era_name])
            cover_url = ""
            for prio_album in priority:
                cover_url = covers.get(_norm(prio_album), "")
                if cover_url:
                    break
            if not cover_url:
                cover_url = album_data["cover_url"]
            eras[era_name] = {
                "album":         era_name,
                "streams":       0,
                "daily_streams": 0,
                "yest_daily":    0,
                "week_daily":    0,
                "cover_url":     cover_url,
            }
        eras[era_name]["streams"]       += album_data["streams"]
        eras[era_name]["daily_streams"] += album_data["daily_streams"]
        if eras[era_name]["yest_daily"] is not None and album_data["yest_daily"] is not None:
            eras[era_name]["yest_daily"] += album_data["yest_daily"]
        else:
            eras[era_name]["yest_daily"] = None
        if eras[era_name]["week_daily"] is not None and album_data["week_daily"] is not None:
            eras[era_name]["week_daily"] += album_data["week_daily"]
        else:
            eras[era_name]["week_daily"] = None

    yest_ranked = sorted(
        [r for r in eras.values() if r.get("yest_daily") and not _is_unranked_era(r["album"])],
        key=lambda r: r["yest_daily"],
        reverse=True,
    )
    yest_rank_by_album = {r["album"]: i + 1 for i, r in enumerate(yest_ranked)}

    rows = sorted(
        eras.values(),
        key=lambda r: (_is_unranked_era(r["album"]), -int(r["daily_streams"] or 0), r["album"]),
    )
    rank = 0
    for row in rows:
        if _is_unranked_era(row["album"]):
            row["rank"] = None
            row["prev_rank"] = None
            continue
        rank += 1
        row["rank"] = rank
        row["prev_rank"] = yest_rank_by_album.get(row["album"])
    _attach_non_album_collage(rows, track_map, today)
    return rows


def _attach_non_album_collage(rows: list[dict], track_map: dict, today: dict) -> None:
    """Pose un collage 2x2 de covers aléatoires sur la ligne de l'ère Non-Album."""
    collage = pick_non_album_collage(track_map, today, n=4)
    if not collage:
        return
    for row in rows:
        if _is_unranked_era(row.get("album", "")):
            row["collage_covers"] = collage


def prefetch_covers(rows: list[dict]) -> dict[str, str]:
    urls = {r["cover_url"] for r in rows if r["cover_url"]}
    for r in rows:
        for u in r.get("collage_covers") or []:
            urls.add(u)
    result: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(download_as_data_uri, u): u for u in urls}
        for fut, url in futures.items():
            data_uri = fut.result()
            if data_uri:
                result[url] = data_uri
    return result


# ---------------------------------------------------------------------------
# HTML builders
# ---------------------------------------------------------------------------

def _album_best_day_labels(rows: list[dict], target_date: str) -> dict[str, str]:
    """{row['album'] -> "* since ..." marker} for the Top Eras card: an era's
    combined (all-versions) daily hitting a best-day-since record. Combined
    album records ARE surfaced here (unlike per-song views) since the era row
    itself is the combined total."""
    labels: dict[str, str] = {}
    try:
        track_map = load_album_track_map()
        history = best_day_since.load_history()
        target = date_cls.fromisoformat(target_date)
        by_key: dict[str, list[str]] = {}
        for track_id, info in track_map.items():
            key = best_day_since.era_key(info.get("album"))
            if key:
                by_key.setdefault(key, []).append(track_id)
        label_by_key: dict[str, str] = {}
        for key, track_ids in by_key.items():
            if len(track_ids) < 2:
                continue
            row = best_day_since.compute_album_best_day_since(key, track_ids, history, target)
            if row and best_day_since.passes_filters(row, min_days=best_day_since.DEFAULT_MIN_DAYS):
                text = best_day_since.best_day_marker_text(row)
                if text:
                    label_by_key[key] = text
        for row in rows:
            key = best_day_since.era_key(row.get("album"))
            if key in label_by_key:
                labels[row["album"]] = label_by_key[key]
    except Exception as exc:  # a marker lookup must never block the card
        print(f"[albums_image] best-day markers unavailable ({exc}).")
    return labels


def build_rows_html(rows: list[dict], image_cache: dict[str, str], total_row: dict | None = None,
                    best_day_labels: dict[str, str] | None = None) -> str:
    best_day_labels = best_day_labels or {}
    html = ""
    render_rows = rows + ([total_row] if total_row else [])
    for i, row in enumerate(render_rows):
        rank = row.get("rank")
        album = row["album"]
        daily = row["daily_streams"]
        total = row["streams"]
        yest  = row["yest_daily"]
        week  = row["week_daily"]
        cover = row["cover_url"]

        cover_uri = image_cache.get(cover, cover) if cover else ""
        collage = row.get("collage_covers") or []
        if row.get("is_total"):
            art_html = '<div class="ledger-art-spacer"></div>'
        elif collage:
            tiles = "".join(
                f'<img src="{image_cache.get(u, u)}" />' for u in collage[:4]
            )
            art_html = f'<div class="ledger-art-collage">{tiles}</div>'
        else:
            art_html = (
                f'<img class="ledger-art" src="{cover_uri}" />'
                if cover_uri else '<div class="ledger-art-ph"></div>'
            )

        delta_num, delta_pct, delta_cls = fmt_delta(daily, yest)
        week_num, week_pct, week_cls = fmt_delta(daily, week)
        rank_label = f"{rank}" if rank else ""
        chg_text, chg_css = rank_change(rank, row.get("prev_rank")) if rank else ("", "neutral")
        daily_signed, _ = fmt_signed(daily)

        rank_color = era_accent_color(album) or dominant_color_from_data_uri(cover_uri)
        rank_style = f' style="color:{rank_color}"' if rank_color else ""

        row_cls = "ledger-row ledger-row-total" if row.get("is_total") else "ledger-row"
        name_html = album if row.get("is_total") else ledger_name_with_best_day(album, best_day_labels.get(album))
        html += f"""<div class="{row_cls}">
  <div class="ledger-rank"{rank_style}>{rank_label}</div>
  <div class="ledger-chg {chg_css}">{chg_text}</div>
  <div class="ledger-entity">
    {art_html}
    <div class="ledger-info">
      <div class="ledger-name">{name_html}</div>
    </div>
  </div>
  <div class="ledger-num"><span class="ledger-daily">{daily_signed}</span></div>
  <div class="ledger-num">
    <div class="ledger-delta {delta_cls}">
      <span class="ledger-delta-num">{delta_num}</span>
      {f'<span class="ledger-delta-pct">{delta_pct}</span>' if delta_pct else ''}
    </div>
  </div>
  <div class="ledger-num">
    <div class="ledger-delta {week_cls}">
      <span class="ledger-delta-num">{week_num}</span>
      {f'<span class="ledger-delta-pct">{week_pct}</span>' if week_pct else ''}
    </div>
  </div>
  <div class="ledger-num"><span class="ledger-total">{fmt_num(total)}</span></div>
</div>
"""
    return html


def build_html(rows: list[dict], target_date: str, image_cache: dict[str, str],
               total_row: dict | None = None,
               masthead_theme: str | None = None) -> str:
    from datetime import datetime
    # Weekday posts (Mon-Fri) -> light theme; weekend posts (Sat/Sun) -> dark.
    if masthead_theme is None:
        masthead_theme = masthead_theme_for_date(target_date)
    date_fmt  = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    best_day_labels = _album_best_day_labels(rows, target_date)
    rows_html = build_rows_html(rows, image_cache, total_row, best_day_labels)

    return build_table_html(
        title="Taylor Swift · Eras on Spotify",
        subtitle=f"Daily Streams · {date_fmt}",
        col_heads=[
            ("#", False), ("+/-", False), ("Album", False),
            ("Daily Streams", True), ("vs Yesterday", True), ("vs Last Week", True), ("Total", True),
        ],
        grid_cols="48px 46px minmax(220px,1fr) 138px 132px 132px 128px",
        rows_html=rows_html,
        handle=HANDLE,
        date_str=date_fmt,
        headers_dir=_headers_dir_for_top_eras(),
        body_width=1000,
        art_size=38,
        col_gap=10,
        extra_css=_ALBUMS_EXTRA_CSS,
        masthead_word="ERAS",
        masthead_theme=masthead_theme,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate(target_date: str | None = None) -> Path:
    if target_date is None:
        target_date = get_latest_date()
    print(f"[albums_image] Date: {target_date}")
    previous_date = str(date_cls.fromisoformat(target_date) - timedelta(days=1))
    history_store.validate_released_active_history_complete(
        target_date,
        comparison_dates=(previous_date,),
        label="albums image",
    )

    covers        = load_covers()
    track_map     = load_album_track_map()
    today, yest, week = load_history(target_date)

    if not today:
        raise ValueError(f"Aucune donnée pour {target_date}")

    rows = build_album_rows(today, yest, week, track_map, covers, target_date=target_date)
    total_row = load_public_total_row(target_date)
    print(f"[albums_image] {len(rows)} albums")
    for r in rows:
        rank_label = f"#{int(r['rank']):2d}" if r.get("rank") else "-- "
        print(f"  {rank_label} {r['album']:<45} daily={r['daily_streams']:>12,}  total={r['streams']:>15,}")
    print(
        f"  == {'Total':<45} daily={total_row['daily_streams']:>12,}  "
        f"total={total_row['streams']:>15,}"
    )

    print("[albums_image] Téléchargement des covers...")
    image_cache = prefetch_covers(rows)
    print(f"  {len(image_cache)} images téléchargées")

    html = build_html(rows, target_date, image_cache, total_row)

    out_dir  = update_streams_dir(target_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "albums_image.png"
    tmp_html = out_dir / "_albums_tmp.html"
    tmp_html.write_text(html, encoding="utf-8")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page(viewport={"width": 1000, "height": 200}, device_scale_factor=2)
            page.goto(f"file:///{tmp_html.as_posix()}", wait_until="load")
            page.wait_for_timeout(300)
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        if tmp_html.exists():
            tmp_html.unlink()

    print(f"[albums_image] Image générée : {out_path}")
    return out_path


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        generate(date_arg)
    except history_store.IncompleteHistoryError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
