#!/usr/bin/env python3
"""Post Spotify total-stream milestone cards when songs cross a threshold."""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
REPO_ROOT = SCRIPT_DIR.parents[4]

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT.parent.parent))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import forecast_milestones  # noqa: E402
import generate_album_update_image  # noqa: E402
import history_store  # noqa: E402
import spotlight  # noqa: E402
from comp import milestone_card  # noqa: E402
from comp.discography import display_title_for_album  # noqa: E402
from core.data_paths import update_streams_dir  # noqa: E402
from core.twitter import post_with_image  # noqa: E402
from post_locks import mark_posted  # noqa: E402
from twitter.albums import album_emoji  # noqa: E402
from twitter.prefixes import spotlight_prefix  # noqa: E402
from twitter.sessions import default_twitter_session  # noqa: E402
from twitter.text import album_stream_milestone_tweet, stream_milestone_tweet  # noqa: E402

TWITTER_SESSION = default_twitter_session(REPO_ROOT)
MILESTONE_STEP = 100_000_000
MAX_STATIC_MILESTONE = 5_000_000_000
MILESTONES = list(range(MILESTONE_STEP, MAX_STATIC_MILESTONE + MILESTONE_STEP, MILESTONE_STEP))
# Album totals (sum of the album's own tracks, = the TOTAL line of the album card) post every 1B.
ALBUM_MILESTONE_STEP = 1_000_000_000
ALBUM_EVENT_PREFIX = "album:"


def _lock_path(stats_date: str) -> Path:
    return update_streams_dir(stats_date) / "stream_milestones_posted.json"


def _load_posted_keys(stats_date: str) -> set[str]:
    path = _lock_path(stats_date)
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    posted = payload.get("posted") if isinstance(payload, dict) else payload
    if not isinstance(posted, list):
        return set()
    return {str(key) for key in posted if str(key).strip()}


def _save_posted_keys(stats_date: str, keys: set[str]) -> None:
    path = _lock_path(stats_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": stats_date,
        "posted": sorted(keys),
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _event_key(event: dict) -> str:
    return f"{event['track_id']}__{int(event['milestone'])}"


def _milestones_crossed(previous_total: int, current_total: int) -> list[int]:
    crossed = [m for m in MILESTONES if previous_total < m <= current_total]
    if current_total > MILESTONES[-1]:
        m = MILESTONES[-1] + MILESTONE_STEP
        while m <= current_total:
            if previous_total < m:
                crossed.append(m)
            m += MILESTONE_STEP
    return crossed


def _active_non_extra_tracks(stats_date: str) -> list[dict]:
    active_ids = history_store.load_active_track_ids_from_discography()
    return [
        track
        for track in history_store.load_tracks_from_discography(active_ids)
        if not track.get("chart_extra")
        and history_store.track_is_released_for_stats_date(track, stats_date)
    ]


def _is_album_name(name: str | None) -> bool:
    text = str(name or "").strip()
    if not text:
        return False
    norm = " ".join(text.casefold().split())
    return norm not in {
        "misc",
        "miscellaneous",
        "standalone",
        "standalone extras",
        "standalone and extras",
        "standalone & extras",
    }


def _album_track_ids(album: str, stats_date: str) -> set[str]:
    active_ids = history_store.load_active_track_ids_from_discography()
    ids: set[str] = set()
    for section in history_store.load_stream_discography_sections_flat():
        if str(section.get("album") or "").strip() != album:
            continue
        for track in section.get("tracks", []):
            if history_store._is_chart_extra(section, track):
                continue
            track_id = history_store.extract_track_id(track.get("url") or track.get("spotify_url"))
            if not track_id or track_id not in active_ids:
                continue
            if not history_store.track_is_released_for_stats_date(track, stats_date):
                continue
            ids.add(track_id)
    return ids


def find_milestone_events(stats_date: str) -> list[dict]:
    previous_date = (date.fromisoformat(stats_date) - timedelta(days=1)).isoformat()
    history = history_store.HistoryIndex.load()
    tracks = _active_non_extra_tracks(stats_date)
    events: list[dict] = []

    for track in tracks:
        track_id = track["track_id"]
        current_total = history.get_total_for_date(track_id, stats_date)
        previous_total = history.get_total_for_date(track_id, previous_date)
        daily = history.get_daily_for_date(track_id, stats_date)
        if current_total is None or previous_total is None:
            continue
        if daily is None:
            continue
        if int(current_total) < int(previous_total):
            continue
        for milestone in _milestones_crossed(int(previous_total), int(current_total)):
            events.append(
                {
                    **track,
                    "streams": int(current_total),
                    "previous_streams": int(previous_total),
                    "daily_streams": int(daily),
                    "milestone": int(milestone),
                    "previous_date": previous_date,
                }
            )

    events.sort(key=lambda row: (int(row["milestone"]), -int(row["streams"]), str(row.get("title") or "").casefold()))
    return events


def _milestone_rank(event: dict, events: list[dict], history: history_store.HistoryIndex, stats_date: str) -> int | None:
    milestone = int(event["milestone"])
    already = 0
    for track in _active_non_extra_tracks(stats_date):
        total = history.get_total_for_date(track["track_id"], event["previous_date"])
        if total is not None and int(total) >= milestone:
            already += 1

    same_day = [row for row in events if int(row["milestone"]) == milestone]
    if len(same_day) > 1:
        print(
            "[stream_milestones] Blocking milestone rank: "
            f"{len(same_day)} songs crossed {milestone:,} on {stats_date}; "
            "same-day order is ambiguous."
        )
        return None
    return already + 1


def _album_milestone_rank(
    event: dict,
    *,
    album: str | None,
    history: history_store.HistoryIndex,
    stats_date: str,
) -> int | None:
    if not _is_album_name(album):
        return None

    milestone = int(event["milestone"])
    album_ids = _album_track_ids(str(album), stats_date)
    if event["track_id"] not in album_ids:
        return None

    already = 0
    for track_id in album_ids:
        if track_id == event["track_id"]:
            continue
        total = history.get_total_for_date(track_id, event["previous_date"])
        if total is not None and int(total) >= milestone:
            already += 1
    return already + 1


def _next_expected_for_milestone(event: dict, forecasts: dict) -> dict | None:
    milestone = int(event["milestone"])
    candidates = []
    for item in forecasts.get("forecasts") or []:
        if item.get("track_id") == event["track_id"]:
            continue
        if int(item.get("next_milestone") or 0) != milestone:
            continue
        expected = (item.get("forecast") or {}).get("expected_date")
        if not expected:
            continue
        candidates.append(item)
    candidates.sort(
        key=lambda item: (
            (item.get("forecast") or {}).get("expected_date") or "9999-12-31",
            int((item.get("forecast") or {}).get("days_left") or 999999),
            str(item.get("title") or "").casefold(),
        )
    )
    return candidates[0] if candidates else None


def _next_expected_for_album_milestone(
    event: dict,
    forecasts: dict,
    *,
    album: str | None,
    tracks_by_id: dict[str, dict],
) -> dict | None:
    if not _is_album_name(album):
        return None

    milestone = int(event["milestone"])
    candidates = []
    for item in forecasts.get("forecasts") or []:
        if item.get("track_id") == event["track_id"]:
            continue
        if int(item.get("next_milestone") or 0) != milestone:
            continue
        expected = (item.get("forecast") or {}).get("expected_date")
        if not expected:
            continue
        track = tracks_by_id.get(str(item.get("track_id") or ""))
        if not track or str(track.get("album") or "").strip() != str(album).strip():
            continue
        candidates.append(item)

    candidates.sort(
        key=lambda item: (
            (item.get("forecast") or {}).get("expected_date") or "9999-12-31",
            int((item.get("forecast") or {}).get("days_left") or 999999),
            str(item.get("title") or "").casefold(),
        )
    )
    return candidates[0] if candidates else None


def _spotlight_track_for_event(event: dict, tracks_by_id: dict[str, dict]) -> dict:
    # Direct id lookup: spotlight.find_track() only recognises ids inside a URL, so a
    # bare id never matched and the album was always empty (album lines never posted).
    found = tracks_by_id.get(event["track_id"])
    if found:
        return found
    return {
        "track_id": event["track_id"],
        "title": event.get("title") or event["track_id"],
        "artist": event.get("primary_artist") or "Taylor Swift",
        "album": "",
        "image_url": event.get("image_url") or "",
        "spotify_url": event.get("spotify_url") or "",
        "type": "album",
        "single_image": "",
        "song_family": "",
    }


def _album_names() -> list[str]:
    names: set[str] = set()
    for path in generate_album_update_image.ALBUMS_DIR.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        name = str(payload.get("album") or "").strip() if isinstance(payload, dict) else ""
        if _is_album_name(name):
            names.add(name)
    return sorted(names, key=str.casefold)


def _album_totals(album: str, stats_date: str, previous_date: str, history: history_store.HistoryIndex) -> tuple[int, int] | None:
    """(total, previous_total) over the album's own tracks — the same tracks as the album
    card's TOTAL. None when any track is missing either day (never a partial sum); a track
    released on stats_date legitimately counts 0 the day before."""
    sections, _canonical = generate_album_update_image.load_album_sections(album, stats_date)
    tracks = generate_album_update_image._album_total_tracks(sections)
    if not tracks:
        return None
    total = previous = 0
    for track in tracks:
        cur = history.get_total_for_date(track["track_id"], stats_date)
        prev = history.get_total_for_date(track["track_id"], previous_date)
        if prev is None and str(track.get("release_date") or "")[:10] == stats_date:
            prev = 0
        if cur is None or prev is None:
            return None
        total += int(cur)
        previous += int(prev)
    return total, previous


def find_album_milestone_events(stats_date: str, history: history_store.HistoryIndex) -> list[dict]:
    previous_date = (date.fromisoformat(stats_date) - timedelta(days=1)).isoformat()
    totals: dict[str, tuple[int, int] | None] = {
        album: _album_totals(album, stats_date, previous_date, history) for album in _album_names()
    }
    events: list[dict] = []
    for album, pair in totals.items():
        if not pair or pair[0] < pair[1]:
            continue
        total, previous = pair
        m = (previous // ALBUM_MILESTONE_STEP + 1) * ALBUM_MILESTONE_STEP
        while m <= total:
            events.append({
                "track_id": f"{ALBUM_EVENT_PREFIX}{album}",
                "album": album,
                "title": album,
                "streams": total,
                "previous_streams": previous,
                "milestone": m,
                "previous_date": previous_date,
            })
            m += ALBUM_MILESTONE_STEP
    for event in events:
        milestone = event["milestone"]
        same_day = [e for e in events if e["milestone"] == milestone]
        if len(same_day) > 1:
            print(f"[stream_milestones] Blocking album milestone rank: {len(same_day)} albums crossed {milestone:,} on {stats_date}.")
            event["rank"] = None
            continue
        already = sum(1 for pair in totals.values() if pair and pair[1] >= milestone)
        event["rank"] = already + 1
    events.sort(key=lambda e: (int(e["milestone"]), str(e["album"]).casefold()))
    return events


def _card_path(stats_date: str, name: str, milestone: int) -> tuple[Path, Path]:
    out_dir = update_streams_dir(stats_date) / "milestones"
    stem = f"{spotlight._clean_title_for_filename(name)}__{milestone}"
    return out_dir / f"{stem}.png", out_dir / f"_{stem}.html"


def _post(tweet: str, image_path: Path, args, event: dict, posted_keys: set[str], newly_posted: set[str]) -> None:
    if not TWITTER_SESSION.exists():
        raise SystemExit(f"Twitter session not found: {TWITTER_SESSION}")
    if not post_with_image(tweet, image_path, TWITTER_SESSION):
        raise SystemExit(f"Failed to post stream milestone: {_event_key(event)}")
    newly_posted.add(_event_key(event))
    # Persist immediately: a later event failing must not cause a retry of
    # the whole script to repost this already-successful milestone.
    posted_keys.add(_event_key(event))
    _save_posted_keys(args.date, posted_keys)
    mark_posted(update_streams_dir(args.date) / "stream_milestones_posted.lock")


def main() -> int:
    parser = argparse.ArgumentParser(description="Post Spotify total-stream milestone cards (songs every 100M, albums every 1B).")
    parser.add_argument("date", help="Stats date YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--post-spacing-seconds", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-post", action="store_true")
    parser.add_argument("--keep-html", action="store_true", help="Keep the card HTML next to the PNG (debug)")
    args = parser.parse_args()

    date.fromisoformat(args.date)
    limit = max(0, int(args.limit))
    if limit == 0:
        print("[stream_milestones] Limit is 0, nothing to do.")
        return 0

    history = history_store.HistoryIndex.load()
    posted_keys = _load_posted_keys(args.date)
    all_events = find_milestone_events(args.date)
    album_events = find_album_milestone_events(args.date, history)
    pending = [e for e in album_events + all_events if args.force or _event_key(e) not in posted_keys][:limit]
    if not pending:
        print(f"[stream_milestones] No new stream milestones found for {args.date}.")
        return 0

    print(f"[stream_milestones] Found {len(pending)} milestone post(s) for {args.date}.")
    forecasts = forecast_milestones.build_forecasts()
    spotlight_tracks = spotlight.load_all_tracks()
    tracks_by_id = {t["track_id"]: t for t in spotlight_tracks}
    covers = spotlight.load_covers()
    newly_posted: set[str] = set()

    for index, event in enumerate(pending, 1):
        if str(event["track_id"]).startswith(ALBUM_EVENT_PREFIX):
            if event.get("rank") is None:
                continue
            album = str(event["album"])
            out_path, tmp_path = _card_path(args.date, album, int(event["milestone"]))
            image_path = milestone_card.write_milestone_png(
                milestone_card.render_album_plaque(
                    album=album,
                    cover_url=covers.get(spotlight._norm(album)),
                    milestone=int(event["milestone"]),
                    rank=int(event["rank"]),
                    stats_date=args.date,
                ),
                out_path, tmp_path, keep_html=args.keep_html,
            )
            tweet = album_stream_milestone_tweet(
                album_title=display_title_for_album(album),
                milestone_streams=int(event["milestone"]),
                milestone_rank=int(event["rank"]),
                prefix=spotlight_prefix(album_emoji(album, fallback="🤍")),
            )
        else:
            rank = _milestone_rank(event, all_events, history, args.date)
            if rank is None:
                continue
            next_expected = _next_expected_for_milestone(event, forecasts)
            if not next_expected:
                print(
                    "[stream_milestones] No forecasted next song for "
                    f"{event['title']} at {int(event['milestone']):,}; posting without the next-song line."
                )

            spot_track = _spotlight_track_for_event(event, tracks_by_id)
            album = spot_track.get("album")
            album_rank = _album_milestone_rank(event, album=album, history=history, stats_date=args.date)
            next_album_expected = _next_expected_for_album_milestone(
                event, forecasts, album=album, tracks_by_id=tracks_by_id,
            )
            has_album_context = _is_album_name(album) and album_rank is not None
            use_album_first = bool(has_album_context and random.choice([False, True]))
            use_album_next = bool(next_album_expected and random.choice([False, True]))

            title = str(event.get("title") or event["track_id"])
            milestone = int(event["milestone"])
            points = history.points_by_track.get(event["track_id"], [])
            common = dict(
                title=title,
                cover_url=spotlight.get_cover_url(spot_track, covers),
                milestone=milestone,
                total=int(event["streams"]),
                daily=int(event["daily_streams"]),
                rank=rank,
                stats_date=args.date,
            )
            # The Climb when our history covers ~the whole climb, Cover Story otherwise.
            if milestone_card.climb_eligible(points, milestone, args.date):
                card_html = milestone_card.render_song_climb(
                    **common,
                    album=album if _is_album_name(album) else None,
                    history_points=points,
                    next_expected=(
                        (str(next_expected.get("title") or next_expected["track_id"]),
                         str((next_expected.get("forecast") or {})["expected_date"]))
                        if next_expected else None
                    ),
                )
                layout = "climb"
            else:
                card_html = milestone_card.render_song_cover_story(
                    **common,
                    prev_step=milestone_card.previous_step_days(points, milestone, args.date),
                )
                layout = "cover"
            out_path, tmp_path = _card_path(args.date, title, milestone)
            image_path = milestone_card.write_milestone_png(card_html, out_path, tmp_path, keep_html=args.keep_html)
            print(f"[stream_milestones] Card layout for {title}: {layout}")
            tweet = stream_milestone_tweet(
                title=title,
                milestone_streams=milestone,
                milestone_rank=rank,
                next_title=(
                    str(next_expected.get("title") or next_expected["track_id"])
                    if next_expected else None
                ),
                next_expected_date=(
                    str((next_expected.get("forecast") or {})["expected_date"])
                    if next_expected else None
                ),
                album_title=str(album) if has_album_context else None,
                album_milestone_rank=album_rank,
                album_first=use_album_first,
                next_album_title=(
                    str(next_album_expected.get("title") or next_album_expected["track_id"])
                    if next_album_expected else None
                ),
                next_album_expected_date=(
                    str((next_album_expected.get("forecast") or {})["expected_date"])
                    if next_album_expected else None
                ),
                album_next=use_album_next,
                prefix=spotlight_prefix(album_emoji(spot_track.get("album"), fallback="🤍")),
            )

        print(f"[stream_milestones] Tweet {index}/{len(pending)} ({len(tweet)} chars):\n{tweet}")
        print(f"[stream_milestones] Image: {image_path}")
        if args.no_post:
            continue
        _post(tweet, image_path, args, event, posted_keys, newly_posted)
        if index < len(pending) and args.post_spacing_seconds > 0:
            time.sleep(args.post_spacing_seconds)

    if args.no_post:
        print("[stream_milestones] Twitter posts skipped (--no-post).")
        return 0
    print(f"[stream_milestones] Posted {len(newly_posted)} milestone post(s) for {args.date}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
