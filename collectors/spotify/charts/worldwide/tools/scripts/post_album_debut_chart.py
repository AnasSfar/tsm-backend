#!/usr/bin/env python3
"""Standalone "album debut" chart posts (Global + US) + priority per-song
multi-country cards, for a day where an album's tracks all share a
release_date matching the chart date — e.g. "The Life of a Showgirl: The
Encore" (4 new + 12 reissued tracks under new Spotify IDs, 2026-09-25).

Trigger: db/discography/albums/*.json scanned for tracks whose release_date
(catalog, already the source of truth used elsewhere for release gating) ==
target date. This doubles as both the "did we really debut today" signal and
the "which tracks belong to this debut" signal — no separate NEW-badge check
needed, since release_date == target_date already implies a real first day
(a track can never have chart data before its own release_date, per the
existing release-date gate in history_store.py / worldwide/daily.py).

Posting order (decision 2026-09-26, "DEBUT phase"): called by
worldwide/daily.py right after its Phase 1 fetch (global/us), AFTER the
priority Global "N New Songs" card and BEFORE the rank-record check and the
regular Global/US daily chart posts. run_all_charts.py calls it again later
as a safety net — the per-region lock makes that a no-op.
  1. Album debut table, Global — only the album's tracks (standard + new
     edition together), ranked by Global position, captioned with the
     combined "filtered streams" total (Spotify Charts' own `streams` field,
     matching the chart shown right below it — NOT the exact streams-pipeline
     total, which is a different, slower-to-land metric).
  2. 30s pause, then the same table for US.
  3. (opt-in, --song-cards) one standalone card per debuting track with all
     its countries. Off by default since 2026-09-26: the priority worldwide
     cards step (post_global_new_releases.py --post-worldwide) already posts
     that exact card; posting both made X reject the second as a duplicate.

Usage:
  python post_album_debut_chart.py 2026-09-25 --post
  python post_album_debut_chart.py 2026-09-25 --no-post
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
COLLECTORS_ROOT = ROOT / "collectors"
SPOTIFY_ROOT = ROOT / "collectors" / "spotify"
GLOBAL_CHART_SCRIPTS = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "script"
WORLDWIDE_SCRIPTS = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "scripts"
for _p in (COLLECTORS_ROOT, SPOTIFY_ROOT, GLOBAL_CHART_SCRIPTS, WORLDWIDE_SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core.data_paths import spotify_chart_dir  # noqa: E402
from core.twitter import TWITTER_TEXT_LIMIT, post_with_image  # noqa: E402
from core.discord_notify import discord_send  # noqa: E402
from comp.discography import display_title_for_album  # noqa: E402
from comp.export_frame import add_export_frame  # noqa: E402
import generate_chart_image as gci  # noqa: E402
import generate_card_images as gcards  # noqa: E402


def _load_album_emoji():
    # collectors/twitter/albums.py loaded BY FILE PATH: importing it as
    # "twitter.albums" would register the collectors/twitter package as
    # "twitter" and shadow core/twitter.py, which generate_card_images
    # imports as "twitter" (ImportError, caught in test 2026-09-26).
    import importlib.util

    spec = importlib.util.spec_from_file_location("_tsm_twitter_albums", COLLECTORS_ROOT / "twitter" / "albums.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.album_emoji


album_emoji = _load_album_emoji()

DISCOGRAPHY_DIR = ROOT / "db" / "discography"
TWITTER_SESSION = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "twitter_session.json"
HANDLE = "@swiftiescharts"

REGIONS = [("global", "Global"), ("us", "US")]
GLOBAL_NEW_RELEASES_SCRIPT = WORLDWIDE_SCRIPTS / "post_global_new_releases.py"

ALBUM_DEBUT_POST_MAX_ATTEMPTS = 3
ALBUM_DEBUT_POST_RETRY_SECONDS = 5
ALBUM_DEBUT_BETWEEN_REGIONS_SECONDS = 30


def _track_id_from_url(value: str | None) -> str:
    match = re.search(r"track/([A-Za-z0-9]+)", value or "")
    return match.group(1) if match else ""


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _fmt(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def find_debut_album(target_date: date) -> tuple[str, list[str]] | None:
    """Scan db/discography/albums/*.json for tracks whose release_date ==
    target_date. Returns (display_title, track_ids) for the first album with
    a match, or None. Generic by design — not specific to any one album."""
    albums_dir = DISCOGRAPHY_DIR / "albums"
    if not albums_dir.exists():
        return None
    for path in sorted(albums_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        album_name = str(payload.get("album") or path.stem)
        track_ids: list[str] = []
        for section in payload.get("sections") or []:
            for track in section.get("tracks") or []:
                if not isinstance(track, dict):
                    continue
                if _parse_date(track.get("release_date")) != target_date:
                    continue
                tid = str(track.get("track_id") or "").strip() or _track_id_from_url(track.get("url"))
                if tid:
                    track_ids.append(tid)
        if track_ids:
            return display_title_for_album(album_name), sorted(set(track_ids))
    return None


def _norm_title(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def find_debut_album_full(target_date: date) -> dict | None:
    """Same trigger as find_debut_album, plus the WHOLE album (every
    section, old tracks included) for the album table (decision 2026-09-26:
    "album debut comprend tous les tracks de l'album meme les anciennes")."""
    albums_dir = DISCOGRAPHY_DIR / "albums"
    if not albums_dir.exists():
        return None
    for path in sorted(albums_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        album_name = str(payload.get("album") or path.stem)
        debut_ids: set[str] = set()
        album_ids: set[str] = set()
        album_titles: set[str] = set()
        for section in payload.get("sections") or []:
            for track in section.get("tracks") or []:
                if not isinstance(track, dict):
                    continue
                tid = str(track.get("track_id") or "").strip() or _track_id_from_url(track.get("url"))
                if tid:
                    album_ids.add(tid)
                hist = track.get("historical_track_ids") or []
                if isinstance(hist, str):
                    hist = hist.split(";")
                album_ids.update(str(h).strip() for h in hist if str(h).strip())
                if track.get("title"):
                    album_titles.add(_norm_title(track.get("title")))
                if tid and _parse_date(track.get("release_date")) == target_date:
                    debut_ids.add(tid)
        if debut_ids:
            album_titles.discard("")
            return {
                "album": album_name,
                "display_title": display_title_for_album(album_name),
                "debut_ids": debut_ids,
                "album_ids": album_ids,
                "album_titles": album_titles,
            }
    return None


def _album_chart_track_ids(region: str, chart_date: str, album_ids: set[str], album_titles: set[str]) -> set[str]:
    """track_ids of this region's chart rows that belong to the album: exact
    track_id match, or exact (normalized) album track title — the chart can
    list a standard track under a re-issued Spotify ID."""
    gci.CHART_REGION = region
    ts_path = gci.date_dir_for(chart_date) / f"ts_chart_{chart_date}.json"
    if not ts_path.exists():
        return set(album_ids)
    ids: set[str] = set(album_ids)
    try:
        rows = json.loads(ts_path.read_text(encoding="utf-8"))
    except Exception:
        return ids
    for row in rows if isinstance(rows, list) else []:
        tid = str(row.get("track_id") or "").strip()
        if tid and _norm_title(row.get("track_name")) in album_titles:
            ids.add(tid)
    return ids


def _prefix(album_name: str) -> str:
    """Song-posting prefix: chart emoji + album emoji (skill song-posting)."""
    return f"\U0001F4C8 | {album_emoji(album_name, fallback='')} ".replace("|  ", "| ")


def new_songs_tweet(rows: list[dict], album_name: str, region_label: str = "Global") -> str:
    """DEBUT new-songs caption (owner 2026-09-26). rows = the new songs
    charting in that region today, any order. Exact values only."""
    rows = sorted(rows, key=lambda r: int(gci.nan_to_none(r.get("rank")) or 9999))
    charts = "global charts" if region_label == "Global" else f"{region_label} charts"
    n = len(rows)
    ranks = [int(gci.nan_to_none(r.get("rank")) or 0) for r in rows]
    best = rows[0]
    best_streams = _fmt(gci.nan_to_none(best.get("streams")))
    if n == 1:
        head = f'"{best.get("track_name")}" debuts at #{ranks[0]} on the {charts} with {best_streams} streams.'
        second = ""
    else:
        if ranks == list(range(1, n + 1)):
            head = f"Taylor Swift occupies the full top {n} of the {charts} with her new songs."
        elif ranks and max(ranks) <= 10:
            head = f"Taylor Swift occupies {n} spots in the top 10 of the {charts} with her new songs."
        else:
            head = f"Taylor Swift debuts {n} new songs on the {charts}."
        second = f' "{best.get("track_name")}" debuts at #{ranks[0]} with {best_streams} streams.'
    lines = [
        f'#{rank} (NEW) {row.get("track_name")} \u2014 {_fmt(gci.nan_to_none(row.get("streams")))}'
        for rank, row in zip(ranks, rows)
    ]
    prefix = _prefix(album_name)

    # Own cap (owner 2026-09-26, X Premium account): core.twitter.TWITTER_TEXT_LIMIT
    # (500). Full text whenever it fits; beyond it (never with 4 songs, ~280
    # chars) drop the 2nd sentence, then trailing lines -> "+N more", so the
    # post can never be rejected for length.
    def build(with_second: bool, keep: int) -> str:
        shown = lines[:keep] + ([f"+{len(lines) - keep} more"] if keep < len(lines) else [])
        return f"{prefix}{head}{second if with_second else ''}\n\n" + "\n".join(shown)

    text = build(True, len(lines))
    if len(text) <= TWITTER_TEXT_LIMIT:
        return text
    for keep in range(len(lines), 0, -1):
        text = build(False, keep)
        if len(text) <= TWITTER_TEXT_LIMIT:
            return text
    return build(False, 1)


def _movement_label(row: dict) -> str | None:
    """Chart movement of one row, from Spotify's own chart fields only:
    NEW / RE / +X (climbed X places) / -X / = . None when the row carries
    no usable signal (never guessed)."""
    movement = str(row.get("movement") or "").strip().upper()
    if row.get("is_re_entry") or movement == "RE":
        return "RE"
    if row.get("is_new") or movement == "NEW":
        return "NEW"
    try:
        previous = int(float(row.get("previous_rank")))
    except (TypeError, ValueError):
        return None
    rank = int(gci.nan_to_none(row.get("rank")) or 0)
    if previous <= 0 or rank <= 0:
        return None
    diff = previous - rank
    if diff > 0:
        return f"+{diff}"
    if diff < 0:
        return f"-{abs(diff)}"
    return "="


def album_table_tweet(rows: list[dict], album_name: str, display_title: str, total_streams: int,
                      region_label: str) -> str:
    """DEBUT steps 3-4 caption: headline + one line per album song on the
    chart, `#R (MOVE) Title — streams` (owner 2026-09-26)."""
    head = (
        f'{_prefix(album_name)}"{display_title}" debuts with {_fmt(total_streams)} streams '
        f"on the {region_label} Spotify charts."
    )
    lines = []
    for row in sorted(rows, key=lambda r: int(gci.nan_to_none(r.get("rank")) or 9999)):
        label = _movement_label(row)
        tag = f" ({label})" if label else ""
        lines.append(
            f'#{int(gci.nan_to_none(row.get("rank")) or 0)}{tag} {row.get("track_name")} '
            f'— {_fmt(gci.nan_to_none(row.get("streams")))}'
        )
    # Own cap core.twitter.TWITTER_TEXT_LIMIT (500): full list whenever it
    # fits, else trailing lines -> "+N more" (the table shows every song).
    for keep in range(len(lines), 0, -1):
        shown = lines[:keep] + ([f"+{len(lines) - keep} more"] if keep < len(lines) else [])
        text = f"{head}\n\n" + "\n".join(shown)
        if len(text) <= TWITTER_TEXT_LIMIT:
            return text
    return head


def _album_lock_path(chart_date: str) -> Path:
    out_dir = spotify_chart_dir("global", chart_date) / "cards"
    return out_dir / "album_debut_posted.json"


def _load_album_lock(chart_date: str) -> set[str]:
    path = _album_lock_path(chart_date)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("posted", []))
    except Exception:
        return set()


def _save_album_lock(chart_date: str, posted: set[str]) -> None:
    path = _album_lock_path(chart_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"date": chart_date, "posted": sorted(posted)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_album_table_image(
    region: str,
    region_label: str,
    chart_date: str,
    track_ids: set[str],
    display_title: str,
    out_path: Path,
) -> tuple[int, list[dict]] | None:
    """Renders the "album debut" table (only this album's tracks, ranked by
    that region's chart position today) via the SAME components as the daily
    regional chart image (comp/tables_image.py, imported through
    generate_chart_image.py) — same look, filtered row set."""
    gci.CHART_REGION = region
    gci.CHART_REGION_NAME = region_label
    date_dir = gci.date_dir_for(chart_date)
    json_path = date_dir / f"ts_chart_{chart_date}.json"
    if not json_path.exists():
        return None
    all_rows = gci.load_json(json_path)
    rows = [r for r in all_rows if str(r.get("track_id") or "").strip() in track_ids]
    if not rows:
        return None
    rows.sort(key=lambda r: gci.nan_to_none(r.get("rank")) or 9999)

    history = gci.load_json(gci.TS_HISTORY_PATH) if region == "global" and gci.TS_HISTORY_PATH.exists() else {}
    cover_map = gci.build_cover_map(gci.COVERS_PATH)
    track_album_map = gci.build_track_album_map(gci.DISCOGRAPHY_ROOT)
    track_image_map = gci._build_track_image_map()
    track_cover_cache = gci.load_track_cover_cache()
    rows_html = gci.build_rows_html(
        rows, history, chart_date, track_album_map, cover_map, track_image_map, gci.ref_streams, track_cover_cache
    )

    date_fmt = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    header_img = gci.pick_header_image(gci.HEADERS_DIR)
    handle_color = "#1db954"
    if header_img:
        handle_color = gci.get_dominant_color(header_img)
        img_url = header_img.as_posix()
        hdr_style = (
            f'style="background-image: linear-gradient(rgba(0,0,0,.45),rgba(0,0,0,.45)),'
            f"url('file:///{img_url}'); background-size:100% 100%;\""
        )
    else:
        hdr_style = 'style="background:linear-gradient(135deg,#1db954 0%,#17a34a 100%);"'

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{gci.CSS}</style></head>
<body>
<div class="container">
  <div class="hdr" {hdr_style}>
    {gci.SPOTIFY_SVG}
    <div>
      <div class="hdr-title">{display_title}</div>
      <div class="hdr-sub">{region_label} Spotify Charts &middot; {date_fmt}</div>
    </div>
  </div>
  {gci.COL_HEADS_HTML}
  {rows_html}
  <div class="ftr">
    <span class="ftr-handle" style="color:{handle_color}">{HANDLE}</span>
    <span class="ftr-date">{date_fmt}</span>
  </div>
</div>
</body></html>"""
    gci.render_html_to_png(html, out_path, out_path.with_name(f"_album_debut_tmp_{os.getpid()}.html"), export_frame=True)

    total_streams = sum(int(gci.nan_to_none(r.get("streams")) or 0) for r in rows)
    return total_streams, rows


def _ts_chart_signature(ts_path: Path) -> dict:
    st = ts_path.stat()
    return {"mtime_ns": st.st_mtime_ns, "size": st.st_size}


def render_album_region_table(
    region: str,
    region_label: str,
    chart_date: str,
    track_ids: set[str],
    display_title: str,
    *,
    kind: str = "album_debut_table",
) -> tuple[str, Path | None, int, list[dict]]:
    """Renders (or reuses) one debut table. `kind` = file stem
    ("album_debut_table" = whole album, "debut_new_songs_table" = new songs).
    Returns (status, image_path, total_streams, rows), status in "ok" /
    "no_data" (chart not collected yet) / "no_rows" (none of these tracks
    chart there). rows = minimal chart rows (rank, track_name, streams,
    track_id) used by the caption.

    Reuse (speed, 2026-09-26): debut_phase.py pre-renders with --render-only
    while step 1 posts. A pre-rendered image is reused only if its sidecar
    meta matches the exact same ts_chart file (mtime_ns + size), title and
    track set; otherwise re-rendered. Render goes to a per-process temp file
    then os.replace(), never a half-written image."""
    gci.CHART_REGION = region
    ts_path = gci.date_dir_for(chart_date) / f"ts_chart_{chart_date}.json"
    if not ts_path.exists():
        return "no_data", None, 0, []
    out_dir = spotify_chart_dir(region, chart_date) / "cards"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{kind}.png"
    meta_path = out_dir / f"{kind}.meta.json"
    signature = _ts_chart_signature(ts_path)
    # "v": bump when the sidecar rows schema changes, so an older pre-render
    # (missing fields) is never reused.
    wanted = {"v": 2, "ts_chart": signature, "display_title": display_title, "track_ids": sorted(track_ids)}

    if out_path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if all(meta.get(k) == v for k, v in wanted.items()):
                print(f"[album-debut] {region_label}: image pre-rendue reutilisee ({kind}, meme ts_chart)")
                return "ok", out_path, int(meta["total_streams"]), list(meta["rows"])
        except Exception:
            pass

    tmp_path = out_dir / f"{kind}.rendering_{os.getpid()}.png"
    try:
        result = build_album_table_image(region, region_label, chart_date, track_ids, display_title, tmp_path)
        if result is None:
            return "no_rows", None, 0, []
        total_streams, rows = result
        if _ts_chart_signature(ts_path) != signature:
            print(f"[album-debut] {region_label}: ts_chart modifie pendant le rendu, nouveau rendu")
            return render_album_region_table(region, region_label, chart_date, track_ids, display_title, kind=kind)
        slim = [
            {
                "rank": int(gci.nan_to_none(r.get("rank")) or 0),
                "track_name": r.get("track_name"),
                "streams": int(gci.nan_to_none(r.get("streams")) or 0),
                "track_id": r.get("track_id"),
                # movement label of the caption list (NEW / RE / +X / -X / =)
                "previous_rank": gci.nan_to_none(r.get("previous_rank")),
                "is_new": bool(gci.nan_to_none(r.get("is_new"))),
                "is_re_entry": bool(gci.nan_to_none(r.get("is_re_entry"))),
                "movement": gci.nan_to_none(r.get("movement")),
            }
            for r in rows
        ]
        os.replace(tmp_path, out_path)
        meta_path.write_text(
            json.dumps({**wanted, "total_streams": total_streams, "rows": slim}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return "ok", out_path, total_streams, slim
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _post_table(region: str, region_label: str, chart_date: str, tweet: str, out_path: Path, *,
                discord_kind: str, discord_key: str, not_before: float | None) -> str:
    if not_before is not None:
        wait = not_before - time.monotonic()
        if wait > 0:
            print(f"[album-debut] pause {wait:.0f}s avant {region_label}...")
            time.sleep(wait)
    discord_send("spotify-charts", [(tweet, out_path)], kind=discord_kind, key=discord_key, thread=region)
    for attempt in range(1, ALBUM_DEBUT_POST_MAX_ATTEMPTS + 1):
        if post_with_image(tweet, out_path, TWITTER_SESSION, priority=0):
            return "posted"
        print(f"[album-debut] {region_label}: echec post, tentative {attempt}/{ALBUM_DEBUT_POST_MAX_ATTEMPTS}")
        if attempt < ALBUM_DEBUT_POST_MAX_ATTEMPTS:
            time.sleep(ALBUM_DEBUT_POST_RETRY_SECONDS)
    print(f"[album-debut] {region_label}: abandon apres {ALBUM_DEBUT_POST_MAX_ATTEMPTS} tentatives")
    return "failed"


def post_album_region_card(
    region: str,
    region_label: str,
    chart_date: str,
    track_ids: set[str],
    display_title: str,
    *,
    album_name: str,
    post: bool,
    not_before: float | None = None,
) -> str:
    """DEBUT step 2: whole-album table of one region. Returns "posted",
    "no_data", "no_rows", "skipped" (--no-post) or "failed"."""
    status, out_path, total_streams, rows = render_album_region_table(
        region, region_label, chart_date, track_ids, display_title
    )
    if status == "no_data":
        print(f"[album-debut] {region_label}: pas de donnee chart pour {chart_date} (pas encore collecte)")
        return "no_data"
    if status == "no_rows":
        print(f"[album-debut] {region_label}: aucun titre de l'album dans ce chart, rien a poster")
        return "no_rows"
    tweet = album_table_tweet(rows, album_name, display_title, total_streams, region_label)
    print(f"[album-debut] {region_label}: {len(rows)} tracks, {_fmt(total_streams)} streams")
    print(f"[album-debut] texte: {tweet}")
    if not post:
        return "skipped"
    return _post_table(region, region_label, chart_date, tweet, out_path, discord_kind="album_debut",
                       discord_key=f"album_debut_{chart_date}_{region}", not_before=not_before)


def post_new_songs_table(
    region: str,
    region_label: str,
    chart_date: str,
    debut_ids: set[str],
    album_name: str,
    *,
    post: bool,
    not_before: float | None = None,
) -> str:
    """DEBUT steps 1-2 (owner 2026-09-26): ONE table of the new songs only
    (not one card per song), Global then US. A single new song on Global
    keeps the existing single card (post_global_new_releases.py --post).
    Returns "posted", "no_data", "no_rows", "skipped" or "failed"."""
    status, out_path, _total, rows = render_album_region_table(
        region, region_label, chart_date, debut_ids, "New Songs", kind="debut_new_songs_table"
    )
    if status == "no_data":
        print(f"[album-debut] nouveaux titres: chart {region_label} {chart_date} pas encore collecte")
        return "no_data"
    if status == "no_rows":
        print(f"[album-debut] nouveaux titres: aucun dans le chart {region_label}, rien a poster")
        return "no_rows"
    if len(rows) == 1 and region == "global":
        print("[album-debut] un seul nouveau titre au Global: card individuelle habituelle")
        if not post:
            return "skipped"
        rc = subprocess.run(
            [sys.executable, str(GLOBAL_NEW_RELEASES_SCRIPT), chart_date, "--post"], cwd=str(ROOT)
        ).returncode
        return "posted" if rc == 0 else "failed"
    tweet = new_songs_tweet(rows, album_name, region_label)
    print(f"[album-debut] nouveaux titres {region_label}: {len(rows)} titres ({len(tweet)} car.)")
    print(f"[album-debut] texte:\n{tweet}")
    if not post:
        return "skipped"
    return _post_table(region, region_label, chart_date, tweet, out_path, discord_kind="global_new_releases",
                       discord_key=f"debut_new_songs_{chart_date}_{region}", not_before=not_before)


def _worldwide_posted_cards_path(chart_date: str) -> Path:
    return spotify_chart_dir("worldwide", chart_date) / "cards" / "posted_cards.json"


def _load_worldwide_posted(chart_date: str) -> set[str]:
    path = _worldwide_posted_cards_path(chart_date)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "posted" in data:
            return set(data["posted"])
        return {Path(f).stem for f in data.get("cards", [])}
    except Exception:
        return set()


def _save_worldwide_posted(chart_date: str, posted: set[str]) -> None:
    path = _worldwide_posted_cards_path(chart_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"date": chart_date, "posted": sorted(posted)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def post_song_debut_cards(chart_date: str, track_ids: list[str], *, post: bool) -> None:
    """One standalone card per debuting track, with every charting country —
    same rendering as generate_card_images.py's regular thread cards
    (_build_card_html/_build_tweet), just posted individually and up front
    instead of bundled later. Writes to the same posted_cards.json lock the
    thread step reads, so it skips these tracks (no double-post) when it
    runs afterward in run_all_charts.py."""
    # _worldwide_data_path falls back to the "latest" pointer (WORLDWIDE_JSON)
    # when no dated snapshot exists yet for chart_date — that pointer always
    # exists once the pipeline has run at least once, so an `.exists()` check
    # alone would silently process a DIFFERENT day's data. Verify the loaded
    # payload's own `date` field actually matches before using it.
    worldwide_json = gcards._worldwide_data_path(chart_date)
    if not worldwide_json.exists():
        print(f"[album-debut] pas de snapshot worldwide pour {chart_date}, skip cards par chanson")
        return
    data = gcards._load_json(worldwide_json)
    if not isinstance(data, dict) or data.get("date") != chart_date:
        print(f"[album-debut] snapshot worldwide pas encore pret pour {chart_date}, skip cards par chanson")
        return
    by_track = data.get("by_track", {})

    songs_raw = gcards._load_json(gcards.SONGS_JSON) if gcards.SONGS_JSON else {}
    songs_list = songs_raw.get("songs", songs_raw) if isinstance(songs_raw, dict) else (songs_raw or [])
    song_meta = {s["track_id"]: s for s in songs_list if isinstance(s, dict) and "track_id" in s}

    prev_by_track = gcards._load_prev_by_track(chart_date)
    already_posted = _load_worldwide_posted(chart_date)
    palette = gcards.THEMES.get("showgirl", next(iter(gcards.THEMES.values())))

    out_dir = spotify_chart_dir("worldwide", chart_date) / "cards"
    out_dir.mkdir(parents=True, exist_ok=True)

    to_post: list[tuple[Path, str, str]] = []
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--force-color-profile=srgb"])
        page = browser.new_page(viewport={"width": 860, "height": 900}, device_scale_factor=3)
        for track_id in track_ids:
            entries = by_track.get(track_id)
            if not entries:
                continue  # not charting anywhere (yet) today
            meta = song_meta.get(track_id, {})
            title_raw = meta.get("title", track_id)
            slug = gcards._slugify(title_raw)
            if slug in already_posted:
                continue
            out_path = out_dir / f"{slug}.png"
            card_palette, _theme = gcards._palette_for_song(meta, palette)
            card_entries = gcards._with_out_regions(entries, prev_by_track.get(track_id, []))
            html_content = gcards._build_card_html(meta, card_entries, card_palette, chart_date)
            try:
                page.set_content(html_content, wait_until="domcontentloaded")
                card = page.locator("#card")
                card.wait_for(state="visible", timeout=5000)
                card.screenshot(path=str(out_path))
                add_export_frame(out_path, device_scale_factor=3)
                tweet_text = gcards._build_tweet(meta, entries, chart_date, None)
                to_post.append((out_path, tweet_text, slug))
                print(f"[album-debut] card generee: {title_raw} ({len(entries)} pays)")
            except Exception as exc:
                print(f"[album-debut] echec card {title_raw!r}: {exc}")
        browser.close()

    if not post or not to_post:
        return

    newly_posted: set[str] = set()
    for out_path, tweet_text, slug in to_post:
        discord_send("spotify-charts", [(tweet_text, out_path)], kind="album_debut_song",
                     key=f"album_debut_song_{chart_date}_{slug}", thread="worldwide")
        posted_ok = False
        for attempt in range(1, ALBUM_DEBUT_POST_MAX_ATTEMPTS + 1):
            if post_with_image(tweet_text, out_path, TWITTER_SESSION, priority=0):
                posted_ok = True
                break
            print(f"[album-debut] {slug}: echec post, tentative {attempt}/{ALBUM_DEBUT_POST_MAX_ATTEMPTS}")
            if attempt < ALBUM_DEBUT_POST_MAX_ATTEMPTS:
                time.sleep(ALBUM_DEBUT_POST_RETRY_SECONDS)
        if posted_ok:
            newly_posted.add(slug)
        else:
            print(f"[album-debut] {slug}: abandon apres {ALBUM_DEBUT_POST_MAX_ATTEMPTS} tentatives")

    if newly_posted:
        _save_worldwide_posted(chart_date, already_posted | newly_posted)
        print(f"[album-debut] {len(newly_posted)} card(s) chanson postee(s) standalone")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("--post", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-post even if the lock says today's debut is done")
    parser.add_argument(
        "--song-cards",
        action="store_true",
        help="Also post one standalone multi-country card per debuting track (off by default: "
        "the priority worldwide cards step already posts those)",
    )
    parser.add_argument(
        "--new-songs",
        action="store_true",
        help="DEBUT step 1: one Global table of the new songs only (a single new song keeps its card)",
    )
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Only pre-render the Global/US tables (no post, no lock); used by debut_phase.py",
    )
    args = parser.parse_args()

    target_date = _parse_date(args.date)
    if target_date is None:
        print(f"[album-debut] date invalide: {args.date!r}")
        return 1
    chart_date = target_date.isoformat()

    debut = find_debut_album_full(target_date)
    if debut is None:
        print(f"[album-debut] aucun album avec release_date == {chart_date}, rien a poster")
        return 0
    display_title = debut["display_title"]
    album_name = debut["album"]
    debut_ids: set[str] = debut["debut_ids"]
    print(f"[album-debut] {display_title!r}: {len(debut_ids)} tracks sortis le {chart_date}")

    def album_ids_for(region: str) -> set[str]:
        return _album_chart_track_ids(region, chart_date, debut["album_ids"], debut["album_titles"])

    if args.render_only:
        for region, region_label in REGIONS:
            st, _p, _t, rows = render_album_region_table(
                region, region_label, chart_date, debut_ids, "New Songs", kind="debut_new_songs_table"
            )
            print(f"[album-debut] pre-rendu nouveaux titres {region_label}: {st} ({len(rows)} tracks)")
        for region, region_label in REGIONS:
            st, _p, total, rows = render_album_region_table(
                region, region_label, chart_date, album_ids_for(region), display_title
            )
            print(f"[album-debut] pre-rendu album {region_label}: {st} ({len(rows)} tracks, {_fmt(total)} streams)")
        return 0

    # Lock keys: "<display_title>|new_songs" (step 1), "<display_title>|<region>"
    # (step 2, one per region, so a rerun only posts what is missing). A bare
    # "<display_title>" (old format) means everything is done.
    posted = set() if args.force else _load_album_lock(chart_date)
    if display_title in posted:
        print(f"[album-debut] deja poste pour {chart_date}, skip (--force pour reposter)")
        return 0

    def record(key: str, status: str) -> None:
        if status in ("posted", "no_rows") and args.post:
            posted.add(key)
            _save_album_lock(chart_date, posted)

    if args.new_songs:
        # Steps 1-2: new-songs table Global, then US 30s after the Global post.
        ns_ok = True
        ns_pending = False
        last_ns_post: float | None = None
        for region, region_label in REGIONS:
            key = f"{display_title}|new_songs|{region}"
            if key in posted:
                print(f"[album-debut] tableau nouveaux titres {region_label} deja poste, skip")
                continue
            not_before = last_ns_post + ALBUM_DEBUT_BETWEEN_REGIONS_SECONDS if last_ns_post is not None else None
            status = post_new_songs_table(
                region, region_label, chart_date, debut_ids, album_name, post=args.post, not_before=not_before
            )
            if status == "failed":
                ns_ok = False
            elif status == "no_data":
                ns_pending = True
            if status == "posted":
                last_ns_post = time.monotonic()
            record(key, status)
        if not ns_ok:
            return 1
        return 3 if ns_pending else 0

    ok = True
    pending = False
    last_post_at: float | None = None
    for region, region_label in REGIONS:
        region_key = f"{display_title}|{region}"
        if region_key in posted:
            print(f"[album-debut] {region_label}: deja poste, skip")
            continue
        # Decision 2026-09-26: 30s between the Global and US table posts,
        # counted from the end of the Global post (US renders meanwhile).
        not_before = last_post_at + ALBUM_DEBUT_BETWEEN_REGIONS_SECONDS if last_post_at is not None else None
        status = post_album_region_card(
            region, region_label, chart_date, album_ids_for(region), display_title,
            album_name=album_name, post=args.post, not_before=not_before,
        )
        if status == "failed":
            ok = False
        elif status == "no_data":
            pending = True
        if status == "posted":
            last_post_at = time.monotonic()
        record(region_key, status)

    if args.song_cards:
        post_song_debut_cards(chart_date, sorted(debut_ids), post=args.post)

    if not ok:
        return 1
    if pending:
        return 3  # a region's chart is not collected yet: DEBUT phase not complete
    return 0


if __name__ == "__main__":
    sys.exit(main())
