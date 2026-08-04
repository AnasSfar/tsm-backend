#!/usr/bin/env python3
"""Post Spotify total-stream overtakes between non-extra songs."""
from __future__ import annotations

import argparse
import html
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
REPO_ROOT = SCRIPT_DIR.parents[4]
DB_DIR = REPO_ROOT / "db"
TWITTER_SESSION = ROOT.parent / "charts" / "global" / "tools" / "json" / "twitter_session.json"
HANDLE = "@swiftiescharts"

sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT.parent.parent))
sys.path.insert(0, str(SCRIPT_DIR))

from comp import tables_image  # noqa: E402
from comp.discography import build_cover_map, build_track_album_map  # noqa: E402
from comp.fmt import fmt_num  # noqa: E402
from core.data_paths import update_streams_dir  # noqa: E402
from core.twitter import post_with_image  # noqa: E402
import generate_streams_image  # noqa: E402
import history_store  # noqa: E402
from post_locks import mark_posted  # noqa: E402

HEADERS_DIR = ROOT / "tools" / "headers"
COVERS_PATH = DB_DIR / "discography" / "covers.json"
DISCOGRAPHY_ROOT = DB_DIR / "discography"
DEFAULT_LIMIT = 5

EXTRA_CSS = """
.hdr{padding:24px 28px}
.hdr-title{font-size:25px}
.hdr-sub{font-size:14px}
.data-row.overtaker{
  background:linear-gradient(90deg,#e9fbef 0%,#f8fffb 46%,rgba(255,255,255,.92) 100%);
  border-left:4px solid #1db954;
}
.data-row.passed{
  background:linear-gradient(90deg,#fff5f5 0%,#fffdfd 46%,rgba(255,255,255,.92) 100%);
  border-left:4px solid #d92d20;
}
.col-rank.prev-rank{font-size:14px;color:#667085;letter-spacing:0}
.gap{font-weight:800}.gap.pos{color:#067647}.gap.neg{color:#b42318}
.role{width:78px;display:flex;justify-content:center;font-size:10px;font-weight:900;letter-spacing:.06em;text-transform:uppercase}
.role.overtaker{color:#067647}.role.passed{color:#b42318}.role.context{color:#98a2b3}
"""


def _date_label(stats_date: str) -> str:
    return datetime.strptime(stats_date, "%Y-%m-%d").strftime("%B %d, %Y")


def _tweet_date_label(stats_date: str) -> str:
    day = datetime.strptime(stats_date, "%Y-%m-%d").date()
    return day.strftime("%B %#d, %Y") if sys.platform == "win32" else day.strftime("%B %-d, %Y")


def _event_key(event: dict) -> str:
    return f"{event['overtaker']['track_id']}__over__{event['passed']['track_id']}"


def _lock_path(stats_date: str) -> Path:
    return update_streams_dir(stats_date) / "song_overtakes_posted.json"


def _load_posted_keys(stats_date: str) -> set[str]:
    path = _lock_path(stats_date)
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    keys = payload.get("posted") if isinstance(payload, dict) else payload
    if not isinstance(keys, list):
        return set()
    return {str(key) for key in keys if str(key).strip()}


def _save_posted_keys(stats_date: str, keys: set[str]) -> None:
    path = _lock_path(stats_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": stats_date,
        "posted": sorted(keys),
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _ranking_for_date(tracks: list[dict], history: history_store.HistoryIndex, stats_date: str) -> list[dict]:
    ranked: list[dict] = []
    for track in tracks:
        total = history.get_total_for_date(track["track_id"], stats_date)
        if total is None:
            continue
        daily = history.get_daily_for_date(track["track_id"], stats_date)
        ranked.append({**track, "streams": total, "daily_streams": daily})
    ranked.sort(key=lambda row: (-int(row["streams"]), str(row.get("title") or "").casefold()))
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
    return ranked


def _context_rows(current: list[dict], overtaker_index: int, passed_track_id: str) -> list[dict]:
    wanted: dict[str, dict] = {}
    for idx in (overtaker_index - 1, overtaker_index, overtaker_index + 1, overtaker_index + 2):
        if 0 <= idx < len(current):
            row = current[idx]
            wanted[row["track_id"]] = row
    for row in current:
        if row["track_id"] == passed_track_id:
            wanted[row["track_id"]] = row
            break
    rows = list(wanted.values())
    rows.sort(key=lambda row: int(row["rank"]))
    return rows


def find_overtakes(stats_date: str, *, limit: int = DEFAULT_LIMIT) -> list[dict]:
    previous_date = str(date.fromisoformat(stats_date) - timedelta(days=1))
    active_ids = history_store.load_active_track_ids_from_discography()
    tracks = [
        track for track in history_store.load_tracks_from_discography(active_ids)
        if not track.get("chart_extra")
        and history_store.track_is_released_for_stats_date(track, stats_date)
    ]
    history = history_store.HistoryIndex.load()
    current = _ranking_for_date(tracks, history, stats_date)
    previous = _ranking_for_date(tracks, history, previous_date)
    previous_by_id = {row["track_id"]: row for row in previous}

    events: list[dict] = []
    seen_pairs: set[str] = set()
    for current_index, overtaker in enumerate(current):
        overtaker_prev = previous_by_id.get(overtaker["track_id"])
        if not overtaker_prev:
            continue
        for passed in current[current_index + 1:]:
            passed_prev = previous_by_id.get(passed["track_id"])
            if not passed_prev:
                continue
            if int(overtaker_prev["streams"]) > int(passed_prev["streams"]):
                continue
            if int(overtaker["streams"]) <= int(passed["streams"]):
                continue
            pair_key = f"{overtaker['track_id']}__over__{passed['track_id']}"
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            events.append({
                "overtaker": overtaker,
                "passed": passed,
                "context": _context_rows(current, current_index, passed["track_id"]),
                "previous_date": previous_date,
                "previous_overtaker": overtaker_prev,
                "previous_passed": passed_prev,
                "gap": int(overtaker["streams"]) - int(passed["streams"]),
            })
            break

    events.sort(key=lambda event: (int(event["overtaker"]["rank"]), -int(event["gap"]), str(event["overtaker"].get("title") or "").casefold()))
    return events[:limit]


def _role_for(row: dict, event: dict) -> tuple[str, str]:
    if row["track_id"] == event["overtaker"]["track_id"]:
        return "overtaker", "Passed"
    if row["track_id"] == event["passed"]["track_id"]:
        return "passed", "Overtaken"
    return "context", ""


def _build_rows_html(event: dict, cover_map: dict, track_album_map: dict) -> str:
    rows_html = ""
    previous_rank_by_id = {
        event["previous_overtaker"]["track_id"]: event["previous_overtaker"]["rank"],
        event["previous_passed"]["track_id"]: event["previous_passed"]["rank"],
    }
    for index, row in enumerate(event["context"]):
        role_cls, role_text = _role_for(row, event)
        card_cls = f"data-row {role_cls}" if role_cls in {"overtaker", "passed"} else ("data-row row-odd" if index % 2 else "data-row")
        cover_url = generate_streams_image.get_cover_url(row, cover_map, track_album_map)
        cover = tables_image.url_to_data_uri(cover_url) if cover_url else ""
        art_html = f'<img class="art" src="{html.escape(cover, quote=True)}" />' if cover else '<div class="art-ph"></div>'
        previous_rank = previous_rank_by_id.get(row["track_id"])
        previous_rank_text = f"#{previous_rank}" if previous_rank is not None else "-"
        gap = ""
        gap_cls = "neutral"
        if row["track_id"] in {event["overtaker"]["track_id"], event["passed"]["track_id"]}:
            gap_value = int(event["overtaker"]["streams"]) - int(event["passed"]["streams"])
            gap = f"+{fmt_num(gap_value)}" if row["track_id"] == event["overtaker"]["track_id"] else f"-{fmt_num(gap_value)}"
            gap_cls = "pos" if row["track_id"] == event["overtaker"]["track_id"] else "neg"
        rows_html += f"""<div class="{card_cls}">
  <div class="col-rank">#{int(row["rank"])}</div>
  <div class="col-rank prev-rank">{previous_rank_text}</div>
  <div class="role {role_cls}">{role_text}</div>
  <div class="col-entity">
    {art_html}
    <div class="entity-info">
      <div class="entity-name">{html.escape(str(row.get("title") or row["track_id"]))}</div>
      <div class="entity-sub">{html.escape(str(row.get("primary_artist") or "Taylor Swift"))}</div>
    </div>
  </div>
  <div class="col-num">{fmt_num(row.get("daily_streams"))}</div>
  <div class="col-num"><strong>{fmt_num(row.get("streams"))}</strong></div>
  <div class="col-num gap {gap_cls}">{gap}</div>
</div>
"""
    return rows_html


def render_overtake_image(event: dict, stats_date: str) -> Path:
    cover_map = build_cover_map(COVERS_PATH)
    track_album_map = build_track_album_map(DISCOGRAPHY_ROOT)
    rows_html = _build_rows_html(event, cover_map, track_album_map)
    out_dir = update_streams_dir(stats_date) / "song_overtakes"
    out_dir.mkdir(parents=True, exist_ok=True)
    overtaker_slug = generate_streams_image._norm(event["overtaker"].get("title") or event["overtaker"]["track_id"])
    passed_slug = generate_streams_image._norm(event["passed"].get("title") or event["passed"]["track_id"])
    out_path = out_dir / f"overtake_{stats_date}_{overtaker_slug}_over_{passed_slug}.png"
    tmp_path = out_dir / f"_overtake_{overtaker_slug}_over_{passed_slug}.html"

    html_text = tables_image.build_table_html(
        title="Taylor Swift Spotify Counter",
        subtitle=(
            f"{html.escape(str(event['overtaker'].get('title') or event['overtaker']['track_id']))} "
            f"passed {html.escape(str(event['passed'].get('title') or event['passed']['track_id']))}"
        ),
        col_heads=[("Rank", False), ("Prev", False), ("", False), ("Track", False), ("Daily", True), ("Total", True), ("Gap", True)],
        grid_cols="58px 58px 84px minmax(260px,1fr) 112px 142px 102px",
        rows_html=rows_html,
        handle=HANDLE,
        date_str=_date_label(stats_date),
        headers_dir=HEADERS_DIR,
        body_width=920,
        art_size=50,
        col_gap=8,
        extra_css=EXTRA_CSS,
    )
    return tables_image.render_html_to_png(html_text, out_path, tmp_path, width=920)


def _fmt_tweet_num(value: int | str) -> str:
    return f"{int(value):,}"


def build_tweet(event: dict, stats_date: str) -> str:
    overtaker = event["overtaker"]
    passed = event["passed"]
    return (
        f'"{overtaker["title"]}" has surpassed "{passed["title"]}" '
        f"on Taylor Swift's Spotify Counter as of {_tweet_date_label(stats_date)}.\n\n"
        f"{_fmt_tweet_num(overtaker['streams'])} streams (+{_fmt_tweet_num(event['gap'])})."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Post non-extra song total-stream overtakes.")
    parser.add_argument("date", help="Stats date YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--post-spacing-seconds", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-post", action="store_true")
    args = parser.parse_args()

    date.fromisoformat(args.date)
    limit = max(0, int(args.limit))
    if limit == 0:
        print("[song_overtakes] Limit is 0, nothing to do.")
        return 0

    posted_keys = _load_posted_keys(args.date)
    events = [event for event in find_overtakes(args.date, limit=limit) if args.force or _event_key(event) not in posted_keys]
    if not events:
        print(f"[song_overtakes] No new non-extra song overtakes found for {args.date}.")
        return 0

    print(f"[song_overtakes] Found {len(events)} overtake(s) for {args.date}.")
    newly_posted: set[str] = set()
    for index, event in enumerate(events, 1):
        image_path = render_overtake_image(event, args.date)
        tweet = build_tweet(event, args.date)
        print(f"[song_overtakes] Tweet {index}/{len(events)} ({len(tweet)} chars):\n{tweet}")
        print(f"[song_overtakes] Image: {image_path}")
        if args.no_post:
            continue
        if not TWITTER_SESSION.exists():
            raise SystemExit(f"Twitter session not found: {TWITTER_SESSION}")
        if not post_with_image(tweet, image_path, TWITTER_SESSION):
            raise SystemExit(f"Failed to post overtake: {_event_key(event)}")
        newly_posted.add(_event_key(event))
        if index < len(events) and args.post_spacing_seconds > 0:
            time.sleep(args.post_spacing_seconds)

    if args.no_post:
        print("[song_overtakes] Twitter posts skipped (--no-post).")
        return 0
    if newly_posted:
        _save_posted_keys(args.date, posted_keys | newly_posted)
        mark_posted(update_streams_dir(args.date) / "song_overtakes_posted.lock")
    print(f"[song_overtakes] Posted {len(newly_posted)} overtake(s) for {args.date}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
