#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).resolve()
REPO_ROOT = SCRIPT_DIR.parents[6]
SPOTIFY_ROOT = REPO_ROOT / "collectors" / "spotify"
if str(SPOTIFY_ROOT) not in sys.path:
    sys.path.insert(0, str(SPOTIFY_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.data_paths import WEB_EXPORT_DATA_DIR, spotify_chart_dir  # noqa: E402
from core.twitter import post_with_image  # noqa: E402
from collectors.spotify.charts.artists_global.artist_global_daily import (  # noqa: E402
    API_BASE,
    UA,
    _add_days_at_pos,
    _parse_artist_entries,
)
from collectors.spotify.charts.worldwide.daily import (  # noqa: E402
    _get_bearer_token_and_regions,
)

TS_NAME = "Taylor Swift"
TWITTER_SESSION = REPO_ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "json" / "twitter_session.json"
OUTPUT_NAME = "artist_global_worldwide.json"
LATEST_OUTPUT = WEB_EXPORT_DATA_DIR / "charts_artists_global_worldwide.json"

_TWITTER_POST_LOCK = Path(tempfile.gettempdir()) / "tsm_twitter_post.lock"
_LOCK_TIMEOUT = 15 * 60


def _wait_for_twitter_lock() -> None:
    start = time.time()
    while True:
        try:
            fd = os.open(str(_TWITTER_POST_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
            os.close(fd)
            return
        except FileExistsError:
            if time.time() - start > _LOCK_TIMEOUT:
                print("[WARN] Twitter post lock timeout - forcing continue")
                return
            time.sleep(2)


def _release_twitter_lock() -> None:
    try:
        _TWITTER_POST_LOCK.unlink()
    except FileNotFoundError:
        pass


def _history_path(chart_date: str) -> Path:
    return spotify_chart_dir("artists_global", chart_date) / OUTPUT_NAME


def _chart_id(region: str) -> str:
    return "artist-global-daily" if region == "global" else f"artist-{region}-daily"


def _find_taylor(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((row for row in rows if str(row.get("artist_name", "")).lower() == TS_NAME.lower()), None)


async def _fetch_region(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    token: str,
    chart_date: str,
    region: str,
    region_name: str,
) -> dict[str, Any] | None:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Referer": "https://charts.spotify.com/",
        "User-Agent": UA,
    }
    url = f"{API_BASE}/{_chart_id(region)}/{chart_date}"
    async with sem:
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 404:
                    print(f"  [{region:>6}] no artist chart")
                    return None
                if resp.status != 200:
                    print(f"  [{region:>6}] HTTP {resp.status}")
                    return None
                data = await resp.json(content_type=None)
        except Exception as exc:
            print(f"  [{region:>6}] error: {exc}")
            return None

    ts_artist = _find_taylor(_parse_artist_entries(data))
    if not ts_artist:
        print(f"  [{region:>6}] Taylor Swift not charting")
        return None
    ts_artist["country"] = region
    ts_artist["country_name"] = region_name
    print(f"  [{region:>6}] Taylor Swift #{ts_artist['rank']}")
    return ts_artist


async def _fetch_all(chart_date: str, token: str, regions: dict[str, str], limit: int) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(limit)
    connector = aiohttp.TCPConnector(limit=max(limit, 1))
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            _fetch_region(session, sem, token, chart_date, region, name)
            for region, name in sorted(regions.items(), key=lambda item: (item[0] != "global", item[1].lower()))
        ]
        results = await asyncio.gather(*tasks)
    rows = [row for row in results if row]
    rows.sort(key=lambda row: (int(row.get("rank") or 9999), str(row.get("country_name") or "")))
    return rows


def _add_worldwide_days_at_pos(rows: list[dict[str, Any]], chart_date: str) -> None:
    _add_days_at_pos(rows, chart_date, "daily")
    try:
        cursor = datetime.strptime(chart_date, "%Y-%m-%d").date() - timedelta(days=1)
    except ValueError:
        return

    counters = {str(row["country"]): 1 for row in rows}
    active = {str(row["country"]): int(row["rank"]) for row in rows if row.get("rank") is not None}
    while active:
        path = _history_path(cursor.isoformat())
        if not path.exists():
            break
        try:
            previous_rows = json.loads(path.read_text(encoding="utf-8-sig")).get("countries") or []
        except Exception:
            break
        previous_by_country = {
            str(row.get("country")): row
            for row in previous_rows
            if isinstance(row, dict) and row.get("country")
        }
        still_active: dict[str, int] = {}
        for country, rank in active.items():
            previous = previous_by_country.get(country)
            if not previous:
                continue
            try:
                previous_rank = int(previous.get("rank"))
            except (TypeError, ValueError):
                continue
            if previous_rank == rank:
                counters[country] += 1
                still_active[country] = rank
        active = still_active
        cursor -= timedelta(days=1)

    for row in rows:
        row["days_at_pos"] = counters.get(str(row.get("country")), 1)


def _format_change(row: dict[str, Any]) -> str:
    previous = row.get("previous_rank")
    rank = row.get("rank")
    if previous is None or rank is None:
        return "NEW"
    delta = int(previous) - int(rank)
    if delta > 0:
        return f"+{delta}"
    if delta < 0:
        return str(delta)
    return "="


def _date_label(chart_date: str) -> str:
    return datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")


def _build_html(rows: list[dict[str, Any]], chart_date: str) -> str:
    date_label = _date_label(chart_date)
    top_rank = min((int(row.get("rank") or 9999) for row in rows), default=0)
    row_html = []
    for row in rows:
        change = _format_change(row)
        change_class = "up" if change.startswith("+") else "down" if change.startswith("-") else "flat"
        row_html.append(
            f"""<div class="row">
  <div class="country">{row.get('country_name')}</div>
  <div class="rank">#{row.get('rank')}</div>
  <div class="change {change_class}">{change}</div>
  <div class="days">{row.get('days_at_pos', 1)}d</div>
</div>"""
        )
    rows_markup = "\n".join(row_html)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
*{{box-sizing:border-box}} body{{margin:0;width:900px;font-family:Inter,Arial,sans-serif;background:#f3f8f5;color:#102018}}
.card{{padding:34px;background:linear-gradient(180deg,#ffffff 0%,#edf8f2 100%);border:1px solid #ccebd8}}
.top{{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}}
.title{{font-size:34px;font-weight:900;letter-spacing:-.02em}}
.sub{{font-size:15px;color:#5d7167;margin-top:5px;font-weight:700}}
.badge{{background:#1db954;color:white;border-radius:999px;padding:12px 16px;font-weight:900;font-size:18px}}
.heads,.row{{display:grid;grid-template-columns:minmax(280px,1fr) 100px 110px 130px;gap:12px;align-items:center}}
.heads{{padding:0 14px 8px;color:#667085;text-transform:uppercase;font-size:12px;font-weight:900;letter-spacing:.06em}}
.row{{padding:12px 14px;border-top:1px solid #d8eadf;font-size:19px;background:rgba(255,255,255,.65)}}
.row:nth-child(odd){{background:rgba(237,248,242,.82)}}
.country{{font-weight:850;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.rank,.change,.days{{text-align:right;font-weight:900}}
.rank{{color:#0f5132}} .up{{color:#067647}} .down{{color:#b42318}} .flat{{color:#8a98a2}}
.foot{{display:flex;justify-content:space-between;margin-top:20px;color:#667085;font-weight:800;font-size:13px}}
</style>
</head>
<body>
<div class="card" id="card">
  <div class="top">
    <div>
      <div class="title">Taylor Swift - Spotify Artist Charts</div>
      <div class="sub">{len(rows)} regions charting - {date_label}</div>
    </div>
    <div class="badge">Best #{top_rank}</div>
  </div>
  <div class="heads"><div>Region</div><div>Pos</div><div>Chg</div><div>Days at Pos</div></div>
  {rows_markup}
  <div class="foot"><span>@tsmusem13</span><span>Global included</span></div>
</div>
</body>
</html>"""


def _render(html: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _wait_for_twitter_lock()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 900, "height": 1600}, device_scale_factor=2)
            page.set_content(html, wait_until="domcontentloaded")
            full_h = page.evaluate("() => document.body.scrollHeight")
            page.set_viewport_size({"width": 900, "height": max(400, min(int(full_h), 6000))})
            page.locator("#card").screenshot(path=str(out_path))
            browser.close()
    finally:
        _release_twitter_lock()
    print(f"[DONE] Image -> {out_path}")


def _tweet(chart_date: str, rows: list[dict[str, Any]]) -> str:
    return (
        f"Taylor Swift on Spotify Artist Charts worldwide yesterday ({_date_label(chart_date)}) :\n\n"
        f"Charting in {len(rows)} regions, including Global."
    )


def run(chart_date: str, *, post: bool, force: bool, limit: int) -> int:
    snapshot_path = _history_path(chart_date)
    image_path = spotify_chart_dir("artists_global", chart_date) / "artist_worldwide_card.png"
    posted_lock = spotify_chart_dir("artists_global", chart_date) / "artist_worldwide_card_posted.lock"

    if snapshot_path.exists() and image_path.exists() and not force:
        data = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
        rows = data.get("countries") or []
        print(f"[SKIP] Artist worldwide data already exists for {chart_date} ({len(rows)} regions)")
    else:
        token, regions = _get_bearer_token_and_regions()
        rows = asyncio.run(_fetch_all(chart_date, token, regions, limit))
        _add_worldwide_days_at_pos(rows, chart_date)
        data = {
            "date": chart_date,
            "artist_name": TS_NAME,
            "countries": rows,
        }
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        LATEST_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        LATEST_OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[DONE] Snapshot -> {snapshot_path}")
        print(f"[DONE] Latest -> {LATEST_OUTPUT}")

    if not rows:
        print("[INFO] Taylor Swift not found in any regional artist chart.")
        return 0

    if force or not image_path.exists():
        _render(_build_html(rows, chart_date), image_path)

    if post:
        if posted_lock.exists() and not force:
            print(f"[SKIP] Artist worldwide card already posted for {chart_date}")
            return 0
        if not TWITTER_SESSION.exists():
            print(f"[WARN] Twitter session missing: {TWITTER_SESSION}")
            return 0
        if post_with_image(_tweet(chart_date, rows), image_path, TWITTER_SESSION):
            posted_lock.touch()
            print("[DONE] Artist worldwide card posted.")
        else:
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and post Taylor Swift worldwide Spotify artist chart card.")
    parser.add_argument("date", nargs="?", help="YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--post", action="store_true", help="Post the generated worldwide artist card.")
    parser.add_argument("--force", action="store_true", help="Refetch/regenerate/repost even if locks exist.")
    parser.add_argument("--limit", type=int, default=int(os.getenv("SPOTIFY_ARTIST_WORLDWIDE_SEMAPHORE", "8")))
    args = parser.parse_args()

    chart_date = args.date or str(date.today() - timedelta(days=1))
    datetime.strptime(chart_date, "%Y-%m-%d")
    return run(chart_date, post=args.post, force=args.force, limit=max(1, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
