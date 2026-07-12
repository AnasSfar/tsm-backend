#!/usr/bin/env python3
"""Post unique stream highlight tweets combining daily, weekly and best-day notes."""
from __future__ import annotations

import argparse
import html
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).resolve().parent          # streams/tools/scripts/
ROOT = SCRIPT_DIR.parents[1]                          # streams/
TWITTER_SESSION = ROOT.parent / "charts" / "global" / "tools" / "json" / "twitter_session.json"
BEST_DAY_THREAD_MIN_DAYS = 30
BEST_DAY_THREAD_MAX_DAYS = 60

sys.path.insert(0, str(ROOT))                         # collectors/spotify/streams/
sys.path.insert(0, str(ROOT.parent))                  # collectors/spotify/
sys.path.insert(0, str(SCRIPT_DIR))                   # streams/tools/scripts/

from core.album_emoji import album_emoji  # noqa: E402
from core.twitter import post_image_thread, post_with_image  # noqa: E402
import best_day_since  # noqa: E402
import generate_streams_image  # noqa: E402
import post_gainer_thread  # noqa: E402
import spotlight  # noqa: E402


def _fmt_int(value: int | None) -> str:
    if value is None:
        return "?"
    return f"{int(value):,}"


def _fmt_pct(value: float) -> str:
    return f"+{value:.1f}%"


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th','th','th','th','th','th'][n % 10]}"


def _short_date_label(label: str) -> str:
    return (
        label.replace("January", "Jan")
        .replace("February", "Feb")
        .replace("March", "Mar")
        .replace("April", "Apr")
        .replace("June", "Jun")
        .replace("July", "Jul")
        .replace("August", "Aug")
        .replace("September", "Sep")
        .replace("October", "Oct")
        .replace("November", "Nov")
        .replace("December", "Dec")
    )


def _compact_best_label(label: str) -> str:
    label = _short_date_label(label)
    if "," in label:
        return label.rsplit(",", 1)[0]
    return label


def _shorten_title(title: str, *, limit: int) -> str:
    title = str(title or "").strip()
    if len(title) <= limit:
        return title
    return title[: max(0, limit - 3)].rstrip() + "..."


def _compact_title(title: str) -> str:
    return (
        str(title or "")
        .replace(" (From The Vault)", "")
        .replace(" (From the Vault)", "")
        .strip()
    )


def _build_combined_tweet(*, target_date: str, daily_rows: list[dict]) -> str:
    d = datetime.strptime(target_date, "%Y-%m-%d").date()
    date_fmt = f"{d.strftime('%A')}, {d.strftime('%B')} {_ordinal(d.day)}, {d.year}"
    return f"ðŸ“ˆ | Taylor Swift's biggest gainers on {date_fmt} â€” daily & weekly."


def _track_entry(row: dict) -> dict:
    track = row["track"]
    return {
        **track,
        "track_id": row["track_id"],
        "title": track.get("title") or row["track_id"],
        "artist": track.get("primary_artist") or track.get("artist") or "Taylor Swift",
        "daily_streams": row.get("daily_today"),
        "streams": row.get("total_today"),
        "image_url": track.get("image_url") or "",
    }


def _fmt_streams(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{int(value):,}".replace(",", "\u202f")


def _fmt_signed_streams(value: int | None) -> str:
    if value is None:
        return "-"
    if value > 0:
        return f"+{_fmt_streams(value)}"
    if value < 0:
        return f"-{_fmt_streams(abs(value))}"
    return "="


def _delta_parts(current: int | None, previous: int | None) -> tuple[str, str, str]:
    if current is None or previous is None or previous <= 0:
        return "-", "", "neutral"
    delta = current - previous
    pct = delta / previous * 100
    pct_text = f"{pct:+.1f}%"
    if pct_text == "-0.0%":
        pct_text = "+0.0%"
    if delta > 0:
        return _fmt_signed_streams(delta), pct_text, "pos"
    if delta < 0:
        return _fmt_signed_streams(delta), pct_text, "neg"
    return "=", pct_text, "neutral"


def _enrich_gainer_rows(rows: list[dict], *, target_date: str) -> list[dict]:
    history = post_gainer_thread.history_store.HistoryIndex.load()
    for row in rows:
        track_id = row["track_id"]
        row["total_today"] = history.get_total_for_date(track_id, target_date)
        row["daily_yesterday"] = post_gainer_thread.history_store._daily_for_spotlight(
            history,
            track_id,
            str(date.fromisoformat(target_date) - timedelta(days=1)),
        )
        row["daily_last_week"] = post_gainer_thread.history_store._daily_for_spotlight(
            history,
            track_id,
            str(date.fromisoformat(target_date) - timedelta(days=7)),
        )
    return rows


def _build_gainer_rows_html(
    rows: list[dict],
    *,
    period: str,
    image_cache: dict[str, str],
    cover_map: dict,
    track_album_map: dict,
) -> str:
    row_html = []
    for index, row in enumerate(rows):
        entry = _track_entry(row)
        title = html.escape(str(entry.get("title") or row["track_id"]))
        artist = html.escape(str(entry.get("artist") or "Taylor Swift"))
        cover_url = generate_streams_image.get_cover_url(entry, cover_map, track_album_map)
        cover = image_cache.get(cover_url, cover_url) if cover_url else ""
        art_html = (
            f'<img class="art" src="{html.escape(cover, quote=True)}" />'
            if cover
            else '<div class="art-ph"></div>'
        )
        daily = row.get("daily_today")
        daily_delta, daily_pct, daily_cls = _delta_parts(daily, row.get("daily_yesterday"))
        week_delta, week_pct, week_cls = _delta_parts(daily, row.get("daily_last_week"))
        compare_delta = "daily-delta" if period == "daily" else "week-delta"
        card_cls = "song-card row-gold" if index == 0 else "song-card row-odd" if index % 2 else "song-card"
        row_html.append(
            f"""<div class="{card_cls}">
  <div class="col-song">
    {art_html}
    <div class="song-info">
      <div class="song-title">{title}</div>
      <div class="song-artist">{artist}</div>
    </div>
  </div>
  <div class="col-num"><strong>{_fmt_streams(daily)}</strong></div>
  <div class="col-num {daily_cls} {compare_delta if period == 'daily' else ''}">
    <div class="delta-wrap">
      <span class="delta-num">{daily_delta}</span>
      {f'<span class="delta-pct">{daily_pct}</span>' if daily_pct else ''}
    </div>
  </div>
  <div class="col-num {week_cls} {compare_delta if period == 'weekly' else ''}">
    <div class="delta-wrap">
      <span class="delta-num">{week_delta}</span>
      {f'<span class="delta-pct">{week_pct}</span>' if week_pct else ''}
    </div>
  </div>
  <div class="col-num">{_fmt_streams(row.get('total_today'))}</div>
</div>"""
        )
    return "\n".join(row_html)


def _build_combined_gainer_table_html(
    daily_rows: list[dict],
    weekly_rows: list[dict],
    *,
    target_date: str,
    image_cache: dict[str, str],
    cover_map: dict,
    track_album_map: dict,
) -> str:
    date_fmt = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    header_img = generate_streams_image._pick_header_image()
    handle_color = "#1db954"
    if header_img:
        handle_color = generate_streams_image._dominant_color(header_img)
        img_url = header_img.as_posix()
        hdr_style = (
            f"style=\"background-image: linear-gradient(rgba(0,0,0,.45),rgba(0,0,0,.45)),"
            f"url('file:///{img_url}'); background-size:cover; background-position:center;\""
        )
    else:
        hdr_style = 'style="background:linear-gradient(135deg,#1db954 0%,#17a34a 100%);"'

    def _section(rows: list[dict], *, period: str, title: str) -> str:
        if not rows:
            return ""
        rows_html = _build_gainer_rows_html(
            rows,
            period=period,
            image_cache=image_cache,
            cover_map=cover_map,
            track_album_map=track_album_map,
        )
        return f"""
    <div class="section-hdr">
      <span class="section-title">{html.escape(title)}</span>
      <span class="badge">Top {len(rows)}</span>
    </div>
    <div class="col-heads">
      <span>Track</span>
      <span class="right">Daily</span>
      <span class="right">Daily Chg</span>
      <span class="right">Weekly Chg</span>
      <span class="right">Total</span>
    </div>
    {rows_html}"""

    sections_html = (
        _section(daily_rows, period="daily", title="Daily Gainers · vs previous day")
        + _section(weekly_rows, period="weekly", title="Weekly Gainers · vs last week")
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  background:
    radial-gradient(circle at 12% 18%, rgba(29,185,84,.13), transparent 30%),
    radial-gradient(circle at 84% 16%, rgba(126,87,255,.10), transparent 32%),
    linear-gradient(180deg,#f4f7f8 0%,#edf3f4 100%);
  width:800px;
  padding:0;
  color:#101828;
}}
.container {{ overflow:hidden; }}
.hdr {{
  padding:22px 26px;
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:18px;
}}
.hdr-brand {{ display:flex; align-items:center; gap:18px; min-width:0; }}
.hdr-logo {{ width:64px; height:64px; flex-shrink:0; }}
.hdr-title {{ color:#fff; font-size:26px; font-weight:800; letter-spacing:0; }}
.hdr-sub {{ color:rgba(255,255,255,.85); font-size:15px; margin-top:5px; }}
.section-hdr {{
  padding:16px 18px 0;
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
}}
.section-title {{
  font-size:15px;
  font-weight:800;
  color:#101828;
  text-transform:uppercase;
  letter-spacing:.04em;
}}
.badge {{
  color:#fff;
  border:1px solid rgba(255,255,255,.35);
  background:rgba(8,14,24,.35);
  padding:7px 12px;
  font-size:12px;
  font-weight:800;
  white-space:nowrap;
}}
.section-hdr .badge {{
  color:#1db954;
  border-color:rgba(29,185,84,.35);
  background:rgba(29,185,84,.08);
}}
.col-heads {{
  display: grid;
  grid-template-columns:minmax(220px,1fr) 104px 94px 94px 92px;
  column-gap:8px;
  padding:9px 18px;
  margin-top:8px;
  background:rgba(241,245,246,.95);
  border-bottom:1px solid rgba(16,24,40,.07);
}}
.col-heads span {{
  font-size:11px;
  font-weight:700;
  text-transform:uppercase;
  letter-spacing:.07em;
  color:#667085;
  display:flex;
  align-items:center;
}}
.col-heads .right {{ justify-content:flex-end; }}
.song-card {{
  display:grid;
  grid-template-columns:minmax(220px,1fr) 104px 94px 94px 92px;
  column-gap:8px;
  align-items:center;
  padding:7px 18px;
  background:rgba(255,255,255,.82);
  border-bottom:1px solid rgba(16,24,40,.05);
}}
.song-card.row-odd {{ background:rgba(248,250,251,.88); }}
.song-card.row-gold {{
  background:linear-gradient(90deg,#fff7d6 0%,#fffdf5 40%,rgba(255,255,255,.92) 100%);
  border-left:3px solid #ebc44c;
}}
.col-song {{ display:flex; align-items:center; gap:12px; min-width:0; }}
.art {{
  width:54px;
  height:54px;
  border-radius:7px;
  flex-shrink:0;
  object-fit:cover;
  box-shadow:0 2px 8px rgba(0,0,0,.12);
}}
.art-ph {{ width:54px; height:54px; border-radius:7px; background:#dde3ea; flex-shrink:0; }}
.song-info {{ min-width:0; }}
.song-title {{
  font-size:15px;
  font-weight:700;
  color:#101828;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}}
.song-artist {{ font-size:13px; color:#667085; margin-top:3px; }}
.col-num {{
  font-size:14px;
  color:#344054;
  font-weight:500;
  display:flex;
  align-items:center;
  justify-content:flex-end;
  font-variant-numeric:tabular-nums;
}}
.pos {{ color:#067647; font-weight:600; }}
.neg {{ color:#b42318; font-weight:600; }}
.neutral {{ color:#667085; }}
.daily-delta,.week-delta {{
  background:rgba(22,163,74,.08);
  border-radius:6px;
  padding:5px 6px;
}}
.delta-wrap {{ display:flex; flex-direction:column; align-items:flex-end; gap:2px; }}
.delta-num {{ font-size:13px; font-weight:600; }}
.delta-pct {{ font-size:11px; font-weight:500; opacity:.85; }}
.ftr {{
  background:rgba(241,245,246,.96);
  padding:11px 20px;
  display:flex;
  justify-content:space-between;
  align-items:center;
  border-top:1px solid rgba(16,24,40,.07);
  margin-top:8px;
}}
.ftr-handle {{ font-size:13px; color:#1db954; font-weight:700; }}
.ftr-date {{ font-size:13px; color:#667085; font-weight:500; }}
</style>
</head>
<body>
  <div class="container">
    <div class="hdr" {hdr_style}>
      <div class="hdr-brand">
        {generate_streams_image.SPOTIFY_SVG}
        <div>
          <div class="hdr-title">Taylor Swift biggest gainers</div>
          <div class="hdr-sub">{html.escape(date_fmt)}</div>
        </div>
      </div>
    </div>
    {sections_html}
    <div class="ftr">
      <span class="ftr-handle" style="color:{handle_color}">@swiftiescharts</span>
      <span class="ftr-date">{html.escape(date_fmt)}</span>
    </div>
  </div>
</body>
</html>"""


def _render_combined_table_image(
    daily_rows: list[dict],
    weekly_rows: list[dict],
    *,
    target_date: str,
    out_dir: Path,
) -> Path:
    daily_rows = _enrich_gainer_rows(daily_rows, target_date=target_date)
    weekly_rows = _enrich_gainer_rows(weekly_rows, target_date=target_date)
    cover_map = generate_streams_image.load_covers()
    track_album_map = generate_streams_image.load_track_album_map()
    image_cache = generate_streams_image.prefetch_images(
        [_track_entry(row) for row in (*daily_rows, *weekly_rows)],
        cover_map,
        track_album_map,
    )
    out_path = out_dir / f"stream_highlights_gainers_{target_date}.png"
    tmp_html = out_dir / "_stream_highlights_gainers.html"
    tmp_html.write_text(
        _build_combined_gainer_table_html(
            daily_rows,
            weekly_rows,
            target_date=target_date,
            image_cache=image_cache,
            cover_map=cover_map,
            track_album_map=track_album_map,
        ),
        encoding="utf-8",
    )
    row_count = len(daily_rows) + len(weekly_rows)
    viewport_height = 400 + row_count * 72
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 880, "height": viewport_height}, device_scale_factor=2)
            page.goto(f"file:///{tmp_html.as_posix()}", wait_until="load")
            page.wait_for_timeout(300)
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        try:
            tmp_html.unlink()
        except FileNotFoundError:
            pass
    return out_path


def _best_day_rows(target_date: str, *, limit: int, min_days: int) -> list[dict]:
    tracks = best_day_since.load_tracks(include_extras=False)
    all_tracks = best_day_since.load_tracks(include_extras=True)
    history = best_day_since.load_history()
    target = date.fromisoformat(target_date)

    rows: list[dict] = []
    seen_families: set[str] = set()
    for track_id, track in tracks.items():
        family = (track.song_family or track_id).strip()
        if family in seen_families:
            continue
        seen_families.add(family)
        row = best_day_since.compute_best_day_since_combined(
            track,
            best_day_since.combined_tracks_for(all_tracks.get(track_id, track), all_tracks),
            history,
            target,
        )
        days_since = row.get("days_since") if row else None
        if (
            row
            and row.get("kind") == "since"
            and best_day_since.passes_filters(row, min_days=min_days)
            and (days_since or 0) <= BEST_DAY_THREAD_MAX_DAYS
        ):
            rows.append(row)

    rows.sort(key=best_day_since.sort_key, reverse=True)
    return rows[:limit]


def _collect_highlight_groups(
    target_date: str,
    *,
    limit: int,
    best_limit: int,
    min_baseline: int,
    min_days: int,
) -> dict[str, list[dict]]:
    daily = post_gainer_thread._pick_gainers(
        target_date,
        compare_days=1,
        limit=limit,
        min_baseline=min_baseline,
    )
    weekly = post_gainer_thread._pick_gainers(
        target_date,
        compare_days=7,
        limit=limit,
        min_baseline=min_baseline,
    )
    best_rows: list[dict] = []

    tracks_by_id = {track["track_id"]: track for track in spotlight.load_all_tracks()}
    best_items: list[dict] = []
    for row in best_rows:
        track_id = row["track_id"]
        track = tracks_by_id.get(track_id) or {"track_id": track_id, "title": row.get("title") or track_id}
        best_items.append({"track_id": track_id, "track": track, "best_day": row})

    return {
        "daily": [
            {"track_id": row["track_id"], "track": row["track"], "daily": row}
            for row in daily
        ],
        "weekly": [
            {"track_id": row["track_id"], "track": row["track"], "weekly": row}
            for row in weekly
        ],
        "best_day": best_items,
    }


def _build_tweet(item: dict, target_date: str) -> str:
    track = item["track"]
    title = track.get("title") or item["track_id"]
    emoji = album_emoji(track.get("album"))
    d = datetime.strptime(target_date, "%Y-%m-%d").date()
    date_fmt = f"{d.strftime('%A')}, {d.strftime('%B')} {_ordinal(d.day)}, {d.year}"
    gainer_periods = [period for period in ("daily", "weekly") if period in item]

    when = f"on {date_fmt}"

    def compose(display_title: str, *, compact: bool = False) -> str:
        if gainer_periods:
            period_label = " & ".join(gainer_periods)
            intro = (
                f'{emoji} "{display_title}" was one of Taylor Swift\'s biggest '
                f"{period_label} gainers by % {when}."
            )
        else:
            intro = f'{emoji} "{display_title}" earned its {best_day_since.row_label(item["best_day"])}.'
        lines = [intro]

        best_label = ""
        if "best_day" in item:
            best_label = (
                _compact_best_label(best_day_since.row_label(item["best_day"]))
                if compact
                else _short_date_label(best_day_since.row_label(item["best_day"]))
            )

        if "daily" in item and "weekly" in item:
            daily = item["daily"]
            weekly = item["weekly"]
            lines.append(
                f"It rose {_fmt_pct(daily['pct'])} vs {when} and {_fmt_pct(weekly['pct'])} vs last week, "
                f"with {_fmt_int(daily['daily_today'])} streams."
            )
        elif "daily" in item:
            row = item["daily"]
            line = (
                f"It rose {_fmt_pct(row['pct'])} vs {when}, with {_fmt_int(row['daily_today'])} streams "
                f"(+{_fmt_int(row['gain'])})"
            )
            if best_label:
                line = (
                    f"It rose {_fmt_pct(row['pct'])} vs {when}, {_fmt_int(row['daily_today'])} streams, "
                    f"earning its {best_label}"
                )
            lines.append(f"{line}.")
        elif "weekly" in item:
            row = item["weekly"]
            line = (
                f"It rose {_fmt_pct(row['pct'])} vs last week, with {_fmt_int(row['daily_today'])} streams "
                f"(+{_fmt_int(row['gain'])})"
            )
            if best_label:
                line = (
                    f"It rose {_fmt_pct(row['pct'])} vs last week, {_fmt_int(row['daily_today'])} streams, "
                    f"earning its {best_label}"
                )
            lines.append(f"{line}.")

        if best_label and not gainer_periods:
            lines[0] = f'{emoji} "{display_title}" earned its {best_label}.'

        lines.append(f"See full track's history here : https://thetsmuseum.app/songs/{item['track_id']}")
        return "\n\n".join(lines)

    tweet = compose(title)
    if len(tweet) <= 280:
        return tweet

    compact_title = _compact_title(title)
    tweet = compose(compact_title, compact=True)
    if len(tweet) <= 280:
        return tweet

    overflow = len(tweet) - 280
    shortened_title = _shorten_title(compact_title, limit=max(18, len(compact_title) - overflow - 3))
    return compose(shortened_title, compact=True)


def _image_for_item(item: dict, target_date: str, covers: dict) -> Path:
    track = item["track"]
    track_id = item["track_id"]
    total_today, total_yesterday, _daily_today, daily_yesterday, daily_last_week = (
        spotlight.load_history_for_tracks([track_id], target_date)
    )
    if total_today is None:
        raise RuntimeError(f"Missing total streams for {track.get('title') or track_id} on {target_date}")

    if "weekly" in item and "daily" not in item:
        comparison_daily = daily_last_week
        comparison_label = "Last Week"
    else:
        comparison_daily = daily_yesterday
        comparison_label = "Yesterday"

    return spotlight.generate_spotlight_image(
        track=track,
        total_scraped=total_today,
        total_yesterday=total_yesterday,
        comparison_daily=comparison_daily,
        comparison_label=comparison_label,
        cover_url=spotlight.get_cover_url(track, covers),
        stats_date=target_date,
        handle="@swiftiescharts",
        combined=False,
        highlight="vs",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Post daily/weekly gainer table images and best-day highlights.")
    parser.add_argument("date", nargs="?", help="Stats date YYYY-MM-DD. Defaults to yesterday.")
    parser.add_argument("--limit", type=int, default=10, help="Top N daily and weekly gainers.")
    parser.add_argument("--best-limit", type=int, default=3, help="Top N best-day-since notes.")
    parser.add_argument("--min-baseline", type=int, default=1000)
    parser.add_argument("--min-days", type=int, default=BEST_DAY_THREAD_MIN_DAYS)
    parser.add_argument("--no-post", action="store_true")
    args = parser.parse_args()

    target_date = args.date or str(date.today() - timedelta(days=1))
    day_dir = post_gainer_thread.history_store.update_streams_dir(target_date)
    day_dir.mkdir(parents=True, exist_ok=True)

    if not args.no_post and not TWITTER_SESSION.exists():
        print(f"ERROR: Twitter session not found at {TWITTER_SESSION}")
        return 1

    groups = _collect_highlight_groups(
        target_date,
        limit=max(0, int(args.limit)),
        best_limit=max(0, int(args.best_limit)),
        min_baseline=max(0, int(args.min_baseline)),
        min_days=max(0, int(args.min_days)),
    )
    daily_rows = [item["daily"] for item in groups["daily"]]
    weekly_rows = [item["weekly"] for item in groups["weekly"]]
    if not daily_rows and not weekly_rows:
        print(f"[stream_highlights] No highlights found for {target_date}.")
        return 0

    lock = day_dir / "stream_highlights_posted.lock"
    if lock.exists() and not args.no_post:
        print(f"[stream_highlights] Gainers table already posted for {target_date}, skipping.")
        return 0

    tweet = _build_combined_tweet(target_date=target_date, daily_rows=daily_rows)
    image_path = _render_combined_table_image(daily_rows, weekly_rows, target_date=target_date, out_dir=day_dir)
    print(f"[stream_highlights] Combined gainers table ({len(tweet)} chars):\n{tweet}")
    print(f"[stream_highlights] Image: {image_path}")
    for rank, row in enumerate(daily_rows, 1):
        print(f"[stream_highlights] daily gainers #{rank}: {row['track'].get('title')} {_fmt_pct(row['pct'])}")
    for rank, row in enumerate(weekly_rows, 1):
        print(f"[stream_highlights] weekly gainers #{rank}: {row['track'].get('title')} {_fmt_pct(row['pct'])}")

    if args.no_post:
        print("[stream_highlights] Combined gainers table post skipped (--no-post).")
        return 0

    if not post_with_image(tweet, image_path, TWITTER_SESSION):
        print("[stream_highlights] Failed to post combined gainers table.")
        return 1
    lock.touch()
    print(f"[stream_highlights] Posted combined gainers table for {target_date}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
