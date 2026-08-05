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
GROUP_RANK_PROXIMITY = 2

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
.rank-delta{font-size:15px;font-weight:900;letter-spacing:0}
.rank-delta.pos{color:#067647}.rank-delta.neg{color:#b42318}.rank-delta.neutral{color:#667085}
.gap{font-weight:900}.gap.pos{color:#067647}.gap.neutral{color:#667085}
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


def _row_with_rank_context(current: list[dict], previous_by_id: dict[str, dict], index: int) -> dict:
    row = dict(current[index])
    previous = previous_by_id.get(row["track_id"])
    row["previous_rank"] = previous.get("rank") if previous else None
    next_row = current[index + 1] if index + 1 < len(current) else None
    row["next_gap"] = int(row["streams"]) - int(next_row["streams"]) if next_row else None
    return row


def _context_rows(current: list[dict], previous_by_id: dict[str, dict], overtaker_index: int, passed_track_id: str) -> list[dict]:
    passed_index = next((idx for idx, row in enumerate(current) if row["track_id"] == passed_track_id), None)
    wanted: dict[str, dict] = {}
    for idx in (overtaker_index - 1, overtaker_index, passed_index, (passed_index + 1 if passed_index is not None else None)):
        if idx is None or not (0 <= idx < len(current)):
            continue
        row = _row_with_rank_context(current, previous_by_id, idx)
        wanted[row["track_id"]] = row
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
                "context": _context_rows(current, previous_by_id, current_index, passed["track_id"]),
                "previous_date": previous_date,
                "previous_overtaker": overtaker_prev,
                "previous_passed": passed_prev,
                "gap": int(overtaker["streams"]) - int(passed["streams"]),
            })
            break

    events.sort(key=lambda event: (int(event["overtaker"]["rank"]), -int(event["gap"]), str(event["overtaker"].get("title") or "").casefold()))
    return events[:limit]


def _event_key_list(group: dict) -> list[str]:
    return [_event_key(event) for event in group["events"]]


def _event_rank_bounds(event: dict) -> tuple[int, int]:
    ranks = [int(row["rank"]) for row in event.get("context") or []]
    return min(ranks), max(ranks)


def group_close_overtakes(events: list[dict]) -> list[dict]:
    groups: list[dict] = []
    for event in sorted(events, key=lambda item: _event_rank_bounds(item)[0]):
        event_min, event_max = _event_rank_bounds(event)
        if not groups or event_min > int(groups[-1]["max_rank"]) + GROUP_RANK_PROXIMITY:
            groups.append({"events": [event], "min_rank": event_min, "max_rank": event_max})
            continue
        groups[-1]["events"].append(event)
        groups[-1]["min_rank"] = min(int(groups[-1]["min_rank"]), event_min)
        groups[-1]["max_rank"] = max(int(groups[-1]["max_rank"]), event_max)

    for group in groups:
        rows_by_id: dict[str, dict] = {}
        for event in group["events"]:
            for row in event.get("context") or []:
                rows_by_id[row["track_id"]] = row
        rows = list(rows_by_id.values())
        rows.sort(key=lambda row: int(row["rank"]))
        group["context"] = rows
    return groups


def _rank_delta(row: dict) -> tuple[str, str]:
    previous_rank = row.get("previous_rank")
    if previous_rank is None:
        return "-", "neutral"
    delta = int(previous_rank) - int(row["rank"])
    if delta > 0:
        return f"+{delta}", "pos"
    if delta < 0:
        return str(delta), "neg"
    return "0", "neutral"


def _row_role_class(row: dict, group: dict) -> str:
    overtaker_ids = {event["overtaker"]["track_id"] for event in group["events"]}
    passed_ids = {event["passed"]["track_id"] for event in group["events"]}
    if row["track_id"] in overtaker_ids:
        return "overtaker"
    if row["track_id"] in passed_ids:
        return "passed"
    return "context"


def _build_rows_html(group: dict, cover_map: dict, track_album_map: dict) -> str:
    rows_html = ""
    for index, row in enumerate(group["context"]):
        role_cls = _row_role_class(row, group)
        card_cls = f"data-row {role_cls}" if role_cls in {"overtaker", "passed"} else ("data-row row-odd" if index % 2 else "data-row")
        cover_url = generate_streams_image.get_cover_url(row, cover_map, track_album_map)
        cover = tables_image.url_to_data_uri(cover_url) if cover_url else ""
        art_html = f'<img class="art" src="{html.escape(cover, quote=True)}" />' if cover else '<div class="art-ph"></div>'
        rank_delta, rank_delta_cls = _rank_delta(row)
        next_gap = row.get("next_gap")
        gap_text = fmt_num(next_gap) if next_gap is not None else "-"
        gap_cls = "pos" if next_gap is not None and int(next_gap) > 0 else "neutral"
        rows_html += f"""<div class="{card_cls}">
  <div class="col-rank">#{int(row["rank"])}</div>
  <div class="col-rank rank-delta {rank_delta_cls}">{rank_delta}</div>
  <div class="col-entity">
    {art_html}
    <div class="entity-info">
      <div class="entity-name">{html.escape(str(row.get("title") or row["track_id"]))}</div>
      <div class="entity-sub">{html.escape(str(row.get("primary_artist") or "Taylor Swift"))}</div>
    </div>
  </div>
  <div class="col-num">{fmt_num(row.get("daily_streams"))}</div>
  <div class="col-num"><strong>{fmt_num(row.get("streams"))}</strong></div>
  <div class="col-num gap {gap_cls}">{gap_text}</div>
</div>
"""
    return rows_html


def _group_subtitle(group: dict) -> str:
    events = group["events"]
    if len(events) == 1:
        event = events[0]
        return (
            f"{html.escape(str(event['overtaker'].get('title') or event['overtaker']['track_id']))} "
            f"passed {html.escape(str(event['passed'].get('title') or event['passed']['track_id']))}"
        )
    return f"{len(events)} songs passed another song on the counter"


def _group_slug(group: dict) -> str:
    event = group["events"][0]
    overtaker_slug = generate_streams_image._norm(event["overtaker"].get("title") or event["overtaker"]["track_id"])
    passed_slug = generate_streams_image._norm(event["passed"].get("title") or event["passed"]["track_id"])
    suffix = f"_and_{len(group['events']) - 1}_more" if len(group["events"]) > 1 else ""
    return f"{overtaker_slug}_over_{passed_slug}{suffix}"


def render_overtake_image(group: dict, stats_date: str) -> Path:
    cover_map = build_cover_map(COVERS_PATH)
    track_album_map = build_track_album_map(DISCOGRAPHY_ROOT)
    rows_html = _build_rows_html(group, cover_map, track_album_map)
    out_dir = update_streams_dir(stats_date) / "song_overtakes"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _group_slug(group)
    out_path = out_dir / f"overtake_{stats_date}_{slug}.png"
    tmp_path = out_dir / f"_overtake_{slug}.html"

    html_text = tables_image.build_table_html(
        title="Taylor Swift Spotify Counter",
        subtitle=_group_subtitle(group),
        col_heads=[("Rank", False), ("Delta", False), ("Track", False), ("Daily", True), ("Total", True), ("Gap", True)],
        grid_cols="58px 58px minmax(300px,1fr) 112px 142px 102px",
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
    song_url = f"https://thetsmuseum.app/songs/{overtaker['track_id']}"
    return (
        f'"{overtaker["title"]}" has surpassed "{passed["title"]}" '
        f"on Taylor Swift's Spotify Counter.\n\n"
        f"{_fmt_tweet_num(overtaker['streams'])} streams\n"
        f"+{_fmt_tweet_num(event['gap'])} ahead as of {_tweet_date_label(stats_date)}.\n\n"
        f"See full track's history here : {song_url}"
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
