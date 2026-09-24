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

Posting order (decision 2026-09-23, must run BEFORE the regular Global/US
daily chart posts and the worldwide cards thread in run_all_charts.py):
  1. Album debut table, Global — only the album's tracks (standard + new
     edition together), ranked by Global position, captioned with the
     combined "filtered streams" total (Spotify Charts' own `streams` field,
     matching the chart shown right below it — NOT the exact streams-pipeline
     total, which is a different, slower-to-land metric).
  2. Same, US.
  3. One standalone card per debuting track, with all its charting
     countries — reuses generate_card_images.py's existing per-track
     rendering (_build_card_html/_build_tweet), just posted individually
     instead of bundled into the ~80-card worldwide thread. Slugs are written
     into the same posted_cards.json the thread step reads, so the later
     `cards` step in run_all_charts.py skips them (no double-post).
  4. (unchanged) the regular Global chart post with every charting TS song —
     already exists (global/daily.py via run_all_charts.py), runs right
     after this script returns.

Usage:
  python post_album_debut_chart.py 2026-09-25 --post
  python post_album_debut_chart.py 2026-09-25 --no-post
"""
from __future__ import annotations

import argparse
import json
import re
import sys
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
from core.twitter import post_with_image  # noqa: E402
from comp.discography import display_title_for_album  # noqa: E402
from comp.export_frame import add_export_frame  # noqa: E402
import generate_chart_image as gci  # noqa: E402
import generate_card_images as gcards  # noqa: E402

DISCOGRAPHY_DIR = ROOT / "db" / "discography"
TWITTER_SESSION = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "twitter_session.json"
HANDLE = "@swiftiescharts"

REGIONS = [("global", "Global"), ("us", "US")]

ALBUM_DEBUT_POST_MAX_ATTEMPTS = 3
ALBUM_DEBUT_POST_RETRY_SECONDS = 30


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
    gci.render_html_to_png(html, out_path, out_path.with_name("_album_debut_tmp.html"), export_frame=True)

    total_streams = sum(int(gci.nan_to_none(r.get("streams")) or 0) for r in rows)
    return total_streams, rows


def post_album_region_card(
    region: str,
    region_label: str,
    chart_date: str,
    track_ids: set[str],
    display_title: str,
    *,
    post: bool,
) -> bool:
    out_dir = spotify_chart_dir(region, chart_date) / "cards"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "album_debut_table.png"

    result = build_album_table_image(region, region_label, chart_date, track_ids, display_title, out_path)
    if result is None:
        print(f"[album-debut] {region_label}: pas de donnee chart pour {chart_date}, skip")
        return True  # not a failure — region just hasn't charted yet
    total_streams, rows = result
    tweet = (
        f'"{display_title}" debuts with {_fmt(total_streams)} streams '
        f"on the {region_label} Spotify charts."
    )
    print(f"[album-debut] {region_label}: {len(rows)} tracks, {_fmt(total_streams)} streams")

    if not post:
        return True

    for attempt in range(1, ALBUM_DEBUT_POST_MAX_ATTEMPTS + 1):
        if post_with_image(tweet, out_path, TWITTER_SESSION, priority=0):
            return True
        print(f"[album-debut] {region_label}: echec post, tentative {attempt}/{ALBUM_DEBUT_POST_MAX_ATTEMPTS}")
        if attempt < ALBUM_DEBUT_POST_MAX_ATTEMPTS:
            import time

            time.sleep(ALBUM_DEBUT_POST_RETRY_SECONDS)
    print(f"[album-debut] {region_label}: abandon apres {ALBUM_DEBUT_POST_MAX_ATTEMPTS} tentatives")
    return False


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
        posted_ok = False
        for attempt in range(1, ALBUM_DEBUT_POST_MAX_ATTEMPTS + 1):
            if post_with_image(tweet_text, out_path, TWITTER_SESSION, priority=0):
                posted_ok = True
                break
            print(f"[album-debut] {slug}: echec post, tentative {attempt}/{ALBUM_DEBUT_POST_MAX_ATTEMPTS}")
            if attempt < ALBUM_DEBUT_POST_MAX_ATTEMPTS:
                import time

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
    args = parser.parse_args()

    target_date = _parse_date(args.date)
    if target_date is None:
        print(f"[album-debut] date invalide: {args.date!r}")
        return 1
    chart_date = target_date.isoformat()

    debut = find_debut_album(target_date)
    if debut is None:
        print(f"[album-debut] aucun album avec release_date == {chart_date}, rien a poster")
        return 0
    display_title, track_ids = debut
    print(f"[album-debut] {display_title!r}: {len(track_ids)} tracks sortis le {chart_date}")

    posted = _load_album_lock(chart_date)
    if display_title in posted and not args.force:
        print(f"[album-debut] deja poste pour {chart_date}, skip (--force pour reposter)")
        return 0

    ok = True
    for region, region_label in REGIONS:
        if not post_album_region_card(region, region_label, chart_date, set(track_ids), display_title, post=args.post):
            ok = False

    post_song_debut_cards(chart_date, track_ids, post=args.post)

    if args.post and ok:
        posted.add(display_title)
        _save_album_lock(chart_date, posted)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
