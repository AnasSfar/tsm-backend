#!/usr/bin/env python3
"""Post Spotify total-stream overtakes between non-extra songs."""
from __future__ import annotations

import argparse
import html
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
REPO_ROOT = SCRIPT_DIR.parents[4]
DB_DIR = REPO_ROOT / "db"
TWITTER_SESSION = ROOT.parent / "charts" / "global" / "tools" / "json" / "twitter_session.json"

sys.path.insert(0, str(ROOT))                 # collectors/spotify/streams/ for best_day_since
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT.parent.parent))
sys.path.insert(0, str(SCRIPT_DIR))

from comp import tables_image  # noqa: E402
from comp.discography import build_cover_map, build_track_album_map  # noqa: E402
from comp.fmt import fmt_delta, fmt_num, fmt_signed  # noqa: E402
from core.data_paths import update_streams_dir  # noqa: E402
from core.twitter import post_with_image  # noqa: E402
from twitter.prefixes import ACCOUNT_HANDLE  # noqa: E402
from twitter.text import song_overtake_tweet  # noqa: E402
import best_day_since  # noqa: E402
import generate_album_update_image  # noqa: E402
import generate_streams_image  # noqa: E402
import history_store  # noqa: E402
from post_locks import mark_posted  # noqa: E402

HANDLE = ACCOUNT_HANDLE

COVERS_PATH = DB_DIR / "discography" / "covers.json"
DISCOGRAPHY_ROOT = DB_DIR / "discography"
DEFAULT_LIMIT = 8
GROUP_RANK_PROXIMITY = 5

# Masthead / ledger card (matches Top Songs / Top Eras / Gainers, 2026-09-06).
# The two songs involved in the overtake get a coloured left rail + faint tint:
# green for the overtaker (moved up), red for the song it passed.
# Total streams is the headline metric here, so it sits first (after Track) and
# is rendered bold; the raw Daily figure is dialed back a notch.
EXTRA_CSS = """
.ledger-row.overtaker{background:rgba(80,200,130,.14);box-shadow:inset 3px 0 0 var(--ledger-pos)}
.ledger-row.passed{background:rgba(224,120,110,.14);box-shadow:inset 3px 0 0 var(--ledger-neg)}
.ledger-chg.chg-eq{color:var(--ledger-faint)}
.ledger-total{font-size:14.5px;font-weight:800;color:var(--ledger-text)}
.ledger-daily{font-size:13px;font-weight:600;color:var(--ledger-muted)}
"""


def _date_label(stats_date: str) -> str:
    return datetime.strptime(stats_date, "%Y-%m-%d").strftime("%B %d, %Y")



def _event_key(event: dict) -> str:
    return f"{event['overtaker']['track_id']}__over__{event['passed']['track_id']}"


def _event_slug(event: dict) -> str:
    overtaker_slug = generate_streams_image._norm(event["overtaker"].get("title") or event["overtaker"]["track_id"])
    passed_slug = generate_streams_image._norm(event["passed"].get("title") or event["passed"]["track_id"])
    return f"{overtaker_slug}_over_{passed_slug}"


def _event_lock_path(stats_date: str, event: dict) -> Path:
    return update_streams_dir(stats_date) / "song_overtakes" / f"{_event_slug(event)}_posted.lock"


def _event_already_posted(stats_date: str, event: dict) -> bool:
    return _event_lock_path(stats_date, event).exists()


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


def _row_with_rank_context(current: list[dict], previous_by_id: dict[str, dict],
                           last_week_by_id: dict[str, dict], index: int) -> dict:
    row = dict(current[index])
    previous = previous_by_id.get(row["track_id"])
    row["previous_rank"] = previous.get("rank") if previous else None
    row["previous_daily_streams"] = previous.get("daily_streams") if previous else None
    last_week = last_week_by_id.get(row["track_id"])
    row["daily_streams_last_week"] = last_week.get("daily_streams") if last_week else None
    return row


def _context_rows(current: list[dict], previous_by_id: dict[str, dict],
                  last_week_by_id: dict[str, dict], overtaker_index: int,
                  passed_track_id: str) -> list[dict]:
    passed_index = next((idx for idx, row in enumerate(current) if row["track_id"] == passed_track_id), None)
    wanted: dict[str, dict] = {}
    for idx in (overtaker_index - 1, overtaker_index, passed_index, (passed_index + 1 if passed_index is not None else None)):
        if idx is None or not (0 <= idx < len(current)):
            continue
        row = _row_with_rank_context(current, previous_by_id, last_week_by_id, idx)
        wanted[row["track_id"]] = row
    rows = list(wanted.values())
    rows.sort(key=lambda row: int(row["rank"]))
    return rows


def find_overtakes(stats_date: str, *, limit: int = DEFAULT_LIMIT) -> list[dict]:
    previous_date = str(date.fromisoformat(stats_date) - timedelta(days=1))
    last_week_date = str(date.fromisoformat(stats_date) - timedelta(days=7))
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
    last_week_by_id = {row["track_id"]: row for row in _ranking_for_date(tracks, history, last_week_date)}

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
                "context": _context_rows(current, previous_by_id, last_week_by_id, current_index, passed["track_id"]),
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


def _ledger_rank_chg(row: dict) -> tuple[str, str]:
    """Rank movement vs the previous day, in the ledger ``.ledger-chg`` idiom
    (triangle + delta, no pill). Overtake context rows are always top-of-catalog
    songs that were ranked yesterday, so a missing previous rank is a rare edge
    case (render a neutral dash rather than a spurious ``NEW``)."""
    previous_rank = row.get("previous_rank")
    if previous_rank is None:
        return "&#8211;", "chg-eq"
    delta = int(previous_rank) - int(row["rank"])
    if delta > 0:
        return f"&#9650; {delta}", "chg-up"
    if delta < 0:
        return f"&#9660; {abs(delta)}", "chg-dn"
    return "=", "chg-eq"


def _row_role_class(row: dict, group: dict) -> str:
    overtaker_ids = {event["overtaker"]["track_id"] for event in group["events"]}
    passed_ids = {event["passed"]["track_id"] for event in group["events"]}
    if row["track_id"] in overtaker_ids:
        return "overtaker"
    if row["track_id"] in passed_ids:
        return "passed"
    return "context"


def _build_rows_html(group: dict, cover_map: dict, track_album_map: dict,
                     best_day_labels: dict[str, str] | None = None) -> str:
    best_day_labels = best_day_labels or {}
    rows_html = ""
    for row in group["context"]:
        role_cls = _row_role_class(row, group)
        row_cls = "ledger-row" + (f" {role_cls}" if role_cls in {"overtaker", "passed"} else "")
        cover_url = generate_streams_image.get_cover_url(row, cover_map, track_album_map)
        cover = tables_image.url_to_data_uri(cover_url) if cover_url else ""
        art_html = (
            f'<img class="ledger-art" src="{html.escape(cover, quote=True)}" />'
            if cover
            else '<div class="ledger-art-ph"></div>'
        )
        chg_text, chg_css = _ledger_rank_chg(row)

        album_name = track_album_map.get(
            generate_streams_image._norm(str(row.get("title") or "")), ""
        )
        rank_color = tables_image.era_accent_color(album_name) or (
            tables_image.dominant_color_from_data_uri(cover) if cover else None
        )
        rank_style = f' style="color:{rank_color}"' if rank_color else ""

        name_html = tables_image.ledger_name_with_best_day(
            html.escape(str(row.get("title") or row["track_id"])),
            best_day_labels.get(row.get("track_id") or ""),
        )
        daily_signed, _ = fmt_signed(row.get("daily_streams"))
        day_num, day_pct, day_cls = fmt_delta(
            row.get("daily_streams"), row.get("previous_daily_streams")
        )
        week_num, week_pct, week_cls = fmt_delta(
            row.get("daily_streams"), row.get("daily_streams_last_week")
        )

        rows_html += f"""<div class="{row_cls}">
  <div class="ledger-rank"{rank_style}>{int(row["rank"])}</div>
  <div class="ledger-chg {chg_css}">{chg_text}</div>
  <div class="ledger-entity">
    {art_html}
    <div class="ledger-info">
      <div class="ledger-name">{name_html}</div>
      <div class="ledger-sub">{html.escape(str(row.get("primary_artist") or "Taylor Swift"))}</div>
    </div>
  </div>
  <div class="ledger-num"><span class="ledger-total">{fmt_num(row.get("streams"))}</span></div>
  <div class="ledger-num"><span class="ledger-daily">{daily_signed}</span></div>
  <div class="ledger-num">
    <div class="ledger-delta {day_cls}">
      <span class="ledger-delta-num">{day_num}</span>
      {f'<span class="ledger-delta-pct">{day_pct}</span>' if day_pct else ''}
    </div>
  </div>
  <div class="ledger-num">
    <div class="ledger-delta {week_cls}">
      <span class="ledger-delta-num">{week_num}</span>
      {f'<span class="ledger-delta-pct">{week_pct}</span>' if week_pct else ''}
    </div>
  </div>
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
    suffix = f"_and_{len(group['events']) - 1}_more" if len(group["events"]) > 1 else ""
    return f"{_event_slug(group['events'][0])}{suffix}"


# --- Same-album overtakes -> album update image (flat total ranking) ----------

_CANONICAL_ALBUM_CACHE: dict[tuple[str, str], str] = {}


def _canonical_album_name(album_name: str, stats_date: str) -> str:
    key = (album_name.strip().casefold(), stats_date)
    if key not in _CANONICAL_ALBUM_CACHE:
        try:
            _sections, canonical = generate_album_update_image.load_album_sections(album_name, stats_date)
            _CANONICAL_ALBUM_CACHE[key] = canonical or album_name
        except Exception:
            _CANONICAL_ALBUM_CACHE[key] = album_name
    return _CANONICAL_ALBUM_CACHE[key]


def _event_album(event: dict, track_album_map: dict) -> str | None:
    """Album name when the overtaker and the passed song sit on the SAME album,
    else None (cross-album overtake -> generic ledger card)."""
    a = track_album_map.get(generate_streams_image._norm(event["overtaker"].get("title") or ""))
    b = track_album_map.get(generate_streams_image._norm(event["passed"].get("title") or ""))
    if a and b and a.strip().casefold() == b.strip().casefold():
        return a
    return None


def same_album_overtake_albums(stats_date: str, *, limit: int = DEFAULT_LIMIT) -> set[str]:
    """Canonical album names with a same-album overtake on ``stats_date``.
    finalize_update drops these from the daily album-update queue — the overtake
    image is that album's update for the day (weekday) and posts anyway on
    weekends."""
    track_album_map = build_track_album_map(DISCOGRAPHY_ROOT)
    out: set[str] = set()
    for event in find_overtakes(stats_date, limit=limit):
        album = _event_album(event, track_album_map)
        if album:
            out.add(_canonical_album_name(album, stats_date))
    return out


def _post_same_album_overtake(
    canonical_album: str,
    events: list[dict],
    stats_date: str,
    *,
    no_post: bool,
    newly_posted: set[str],
) -> None:
    group = {"events": events}
    tweet = song_overtake_tweet(group, stats_date)

    block = generate_album_update_image.holiday_collection_post_block_reason(canonical_album, stats_date)
    if block:
        print(f"[song_overtakes] Same-album overtake ({canonical_album}) not posted: {block}")
        if not no_post:
            for event in events:
                mark_posted(_event_lock_path(stats_date, event))
        return

    highlight: dict[str, str] = {}
    for event in events:
        highlight[event["overtaker"]["track_id"]] = "overtaker"
        highlight.setdefault(event["passed"]["track_id"], "passed")

    image_path = generate_album_update_image.generate(
        canonical_album,
        stats_date,
        flat_rank_by_total=True,
        highlight_track_ids=highlight,
    )
    print(f"[song_overtakes] Same-album overtake image ({canonical_album}): {image_path}")
    print(f"[song_overtakes] Tweet ({len(tweet)} chars):\n{tweet}")
    if no_post:
        return

    if not TWITTER_SESSION.exists():
        raise SystemExit(f"Twitter session not found: {TWITTER_SESSION}")
    if not post_with_image(tweet, image_path, TWITTER_SESSION):
        raise SystemExit(f"Failed to post same-album overtake: {canonical_album}")

    newly_posted.update(_event_key(event) for event in events)
    for event in events:
        mark_posted(_event_lock_path(stats_date, event))
    # Claim the album's daily-update slot so the normal album-update path and the
    # weekday all-albums fallback both skip it (the finalize queue is also
    # pre-filtered, this is the cross-process safety net).
    try:
        generate_album_update_image.album_update_lock_path(canonical_album, stats_date).write_text(
            f"posted via song overtake {stats_date}\n", encoding="utf-8"
        )
    except Exception as exc:
        print(f"[song_overtakes] Could not write album-update lock ({canonical_album}): {exc}")


OVERTAKE_BODY_WIDTH = 940


def render_overtake_image(group: dict, stats_date: str) -> Path:
    cover_map = build_cover_map(COVERS_PATH)
    track_album_map = build_track_album_map(DISCOGRAPHY_ROOT)

    best_day_labels: dict[str, str] = {}
    try:
        best_day_labels = best_day_since.best_day_marker_labels(
            [r.get("track_id") for r in group["context"] if r.get("track_id")],
            date.fromisoformat(stats_date),
        )
    except Exception as exc:  # a marker lookup must never block the card
        print(f"[song_overtakes] best-day markers unavailable ({exc}).")

    rows_html = _build_rows_html(group, cover_map, track_album_map, best_day_labels)
    out_dir = update_streams_dir(stats_date) / "song_overtakes"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _group_slug(group)
    out_path = out_dir / f"overtake_{stats_date}_{slug}.png"
    tmp_path = out_dir / f"_overtake_{slug}.html"

    html_text = tables_image.build_table_html(
        title="Taylor Swift · All-Time Streams",
        subtitle=_group_subtitle(group),
        col_heads=[
            ("Rank", False), ("+/-", False), ("Track", False),
            ("Total", True), ("Daily", True), ("Daily Chg", True), ("Weekly Chg", True),
        ],
        grid_cols="46px 42px minmax(150px,1fr) 116px 100px 92px 92px",
        rows_html=rows_html,
        handle=HANDLE,
        date_str=_date_label(stats_date),
        headers_dir=generate_streams_image._headers_dir_for_top_songs(),
        body_width=OVERTAKE_BODY_WIDTH,
        art_size=44,
        col_gap=8,
        extra_css=EXTRA_CSS,
        masthead_word="STREAMS",
        masthead_theme=tables_image.masthead_theme_for_date(stats_date),
    )
    return tables_image.render_html_to_png(
        html_text, out_path, tmp_path, width=OVERTAKE_BODY_WIDTH
    )

def _fmt_tweet_num(value: int | str) -> str:
    return f"{int(value):,}"


def build_tweet(group: dict, stats_date: str) -> str:
    return song_overtake_tweet(group, stats_date)

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

    events = [
        event for event in find_overtakes(args.date, limit=limit)
        if args.force or not _event_already_posted(args.date, event)
    ]
    if not events:
        print(f"[song_overtakes] No new non-extra song overtakes found for {args.date}.")
        return 0

    # Split: same-album overtakes -> that album's update image (flat total
    # ranking, movement markers); everything else -> the generic ledger card.
    track_album_map = build_track_album_map(DISCOGRAPHY_ROOT)
    same_album: dict[str, list[dict]] = {}
    cross_events: list[dict] = []
    for event in events:
        album = _event_album(event, track_album_map)
        if album:
            same_album.setdefault(_canonical_album_name(album, args.date), []).append(event)
        else:
            cross_events.append(event)

    groups = group_close_overtakes(cross_events)
    print(
        f"[song_overtakes] {len(events)} overtake(s) for {args.date}: "
        f"{len(groups)} ledger group(s), {len(same_album)} same-album album image(s)."
    )

    newly_posted: set[str] = set()
    units_posted = 0
    total_units = len(groups) + len(same_album)

    for group in groups:
        image_path = render_overtake_image(group, args.date)
        tweet = build_tweet(group, args.date)
        print(f"[song_overtakes] Ledger card ({len(tweet)} chars):\n{tweet}")
        print(f"[song_overtakes] Image: {image_path}")
        units_posted += 1
        if args.no_post:
            continue
        if not TWITTER_SESSION.exists():
            raise SystemExit(f"Twitter session not found: {TWITTER_SESSION}")
        if not post_with_image(tweet, image_path, TWITTER_SESSION):
            raise SystemExit(f"Failed to post overtake group: {', '.join(_event_key_list(group))}")
        newly_posted.update(_event_key_list(group))
        # Persist immediately, one lock per song overtake: a later group
        # failing must not cause a retry of the whole script to repost an
        # overtake that already went out successfully.
        for event in group["events"]:
            mark_posted(_event_lock_path(args.date, event))
        if units_posted < total_units and args.post_spacing_seconds > 0:
            time.sleep(args.post_spacing_seconds)

    for canonical_album, alb_events in same_album.items():
        _post_same_album_overtake(
            canonical_album, alb_events, args.date,
            no_post=args.no_post, newly_posted=newly_posted,
        )
        units_posted += 1
        if not args.no_post and units_posted < total_units and args.post_spacing_seconds > 0:
            time.sleep(args.post_spacing_seconds)

    if args.no_post:
        print("[song_overtakes] Twitter posts skipped (--no-post).")
        return 0
    print(f"[song_overtakes] Posted {len(newly_posted)} overtake(s) for {args.date}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
