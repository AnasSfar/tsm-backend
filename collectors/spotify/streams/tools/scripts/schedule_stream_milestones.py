#!/usr/bin/env python3
"""Anticipate milestone posts conservatively, separately from verified stats."""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import post_stream_milestones as posts
import milestone_schedule as model
from core import twitter as twitter_core
from core import x_active_times

MADRID = ZoneInfo("Europe/Madrid")


def choose_slot(target: date, now: datetime, busy: list[datetime]) -> datetime | None:
    return x_active_times.best_engagement_slot(
        target, now=now, busy=busy,
        min_gap_seconds=twitter_core.TWITTER_SCHEDULE_COLLISION_SECONDS,
    )


def candidates(stats_date: str, history) -> list[dict]:
    recent_start = (date.fromisoformat(stats_date) - timedelta(days=13)).isoformat()
    recent_dailies = {
        (tid, p["date"]): p.get("daily_streams")
        for tid, points in history.points_by_track.items() for p in points
        if recent_start <= p["date"] <= stats_date
    }
    rows = []
    for track in posts._active_non_extra_tracks(stats_date):
        rows.append(({**track, "kind": "song"}, {track["track_id"]}, posts.MILESTONE_STEP))
    target_date = (date.fromisoformat(stats_date) + timedelta(days=2)).isoformat()
    for album in posts._album_names():
        sections, _ = posts.generate_album_update_image.load_album_sections(album, stats_date)
        ids = {t["track_id"] for t in posts.generate_album_update_image._album_total_tracks(sections)}
        future_sections, _ = posts.generate_album_update_image.load_album_sections(album, target_date)
        future_ids = {t["track_id"] for t in posts.generate_album_update_image._album_total_tracks(future_sections)}
        if ids != future_ids:
            print(f"[milestone_schedule] PENDING {album}: album membership changes inside forecast window")
            continue
        rows.append(({
            "track_id": f"{posts.ALBUM_EVENT_PREFIX}{album}", "album": album, "title": album, "kind": "album",
        }, ids, posts.ALBUM_MILESTONE_STEP))
    result = []
    for entity, ids, step in rows:
        totals = [history.get_total_for_date(tid, stats_date) for tid in ids]
        if not totals or any(v is None for v in totals):
            print(f"[milestone_schedule] PENDING {entity['title']}: missing current total")
            continue
        current = sum(totals)
        milestone = (current // step + 1) * step
        # Cheap candidate prefilter only. Qualification always uses exact,
        # complete series and held-out errors, never this recent maximum.
        recent = []
        for i in range(14):
            day = (date.fromisoformat(stats_date) - timedelta(days=i)).isoformat()
            values = [recent_dailies.get((tid, day)) for tid in ids]
            if any(v is None for v in values):
                print(f"[milestone_schedule] PENDING {entity['title']}: missing daily on {day}")
                break
            recent.append(sum(values))
        if len(recent) != 14:
            continue
        if milestone - current > 2 * max(recent):
            continue
        try:
            series = model.exact_series(history, ids, stats_date)
            for horizon in (1, 2):
                window = model.crossing_window(series, milestone, horizon)
                if window["eligible"]:
                    result.append({**entity, **window})
                    break
            else:
                print(f"[milestone_schedule] PENDING {entity['title']} {milestone:,}: conservative day-of-crossing bounds do not qualify")
        except model.PendingForecast as exc:
            print(f"[milestone_schedule] PENDING {entity['title']} {milestone:,}: {exc}")
    return result


def reconcile(ledger: dict, history, stats_date: str, *, persist: bool) -> None:
    """Audit scheduled predictions against exact later snapshots, without reposts."""
    changed = False
    for key, event in ledger["events"].items():
        if event["status"] != "scheduled" or event.get("outcome") or event["target_date"] > stats_date:
            continue
        target = event["target_date"]
        prior = (date.fromisoformat(target) - timedelta(days=1)).isoformat()
        if event["kind"] == "album":
            pair = posts._album_totals(event["album"], target, prior, history)
        else:
            cur = history.get_total_for_date(event["track_id"], target)
            prev = history.get_total_for_date(event["track_id"], prior)
            pair = (cur, prev) if cur is not None and prev is not None else None
        if pair is None:
            print(f"[milestone_schedule] PENDING audit {key}: missing exact totals {prior}/{target}")
            continue
        total, previous = pair
        event["outcome"] = "confirmed" if previous < event["milestone"] <= total else "wrong_day"
        event["verification"] = {"target_total": total, "previous_total": previous, "checked_on": stats_date}
        print(f"[milestone_schedule] AUDIT {key}: {event['outcome']} ({previous:,} -> {total:,}, target {target})")
        changed = True
    if changed and persist:
        model.save_ledger(ledger)


def render(event: dict):
    covers = posts.spotlight.load_covers()
    milestone = event["milestone"]
    target = event["target_date"]
    if event["kind"] == "album":
        album = event["album"]
        html = posts.milestone_card.render_album_plaque(
            album=album, cover_url=covers.get(posts.spotlight._norm(album)), milestone=milestone, rank=None, stats_date=target,
        )
        tweet = posts.album_stream_milestone_tweet(
            album_title=posts.display_title_for_album(album), milestone_streams=milestone, milestone_rank=None,
            prefix=posts.spotlight_prefix(posts.album_emoji(album)),
        )
    else:
        tracks = {t["track_id"]: t for t in posts.spotlight.load_all_tracks()}
        track = posts._spotlight_track_for_event(event, tracks)
        html = posts.milestone_card.render_song_cover_story(
            title=event["title"], cover_url=posts.spotlight.get_cover_url(track, covers), milestone=milestone,
            total=None, daily=None, rank=None, stats_date=target, prev_step=None,
        )
        tweet = posts.stream_milestone_tweet(
            title=event["title"], milestone_streams=milestone, milestone_rank=None,
            prefix=posts.spotlight_prefix(posts.album_emoji(track.get("album"))),
        )
    png, temporary = posts._card_path(target, event["title"], milestone)
    return tweet, posts.milestone_card.write_milestone_png(html, png, temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="Last verified stats date")
    parser.add_argument("--no-post", action="store_true")
    parser.add_argument("--plan-only", action="store_true", help="Print candidates without rendering, posting or writing locks")
    args = parser.parse_args()
    if args.no_post or args.plan_only:
        return run(args)
    with model.scheduling_lock():
        return run(args)


def run(args) -> int:
    stats_day = date.fromisoformat(args.date)
    now = datetime.now(MADRID)
    # Throwbacks/late catch-up runs cannot create fresh forecasts using stale
    # totals. Independent of the machine's timezone and inherited pacing env.
    if not 0 <= (now.date() - stats_day).days <= 1:
        print(f"[milestone_schedule] PENDING: stale/future base date {args.date}, today {now.date()}")
        return 0
    history = posts.history_store.HistoryIndex.load()
    ledger = model.load_ledger()
    reconcile(ledger, history, args.date, persist=not (args.no_post or args.plan_only))
    reserved = model.reserved_keys()
    for event in candidates(args.date, history):
        key = posts._event_key(event)
        if key in reserved:
            continue
        target = date.fromisoformat(event["target_date"])
        busy = [e["at"] for e in twitter_core.scheduled_entries(twitter_core._account_key(posts.TWITTER_SESSION))]
        wanted = choose_slot(target, datetime.now(MADRID), busy)
        deadline = datetime.combine(target, time(23, 59), MADRID)
        if wanted is None:
            print(f"[milestone_schedule] PENDING {key}: no free future slot on target day; wait for verified milestone")
            continue
        timing = {
            "strategy": "best_available_audience_activity",
            "activity_source_date": x_active_times.ACTIVITY_SOURCE_DATE,
            "audience_timezone": str(x_active_times.AUDIENCE_TIMEZONE),
            "planned_activity_weight": x_active_times.engagement_weight(wanted),
        }
        print("PLAN_JSON: " + json.dumps({**event, "scheduled_at": wanted.isoformat(), "timing": timing}, ensure_ascii=False))
        if args.plan_only:
            continue
        tweet, png = render(event)
        print(f"[milestone_schedule] {wanted.isoformat()}\n{tweet}\n{png}")
        if args.no_post:
            continue
        if not posts.TWITTER_SESSION.exists():
            raise SystemExit(f"Twitter session not found: {posts.TWITTER_SESSION}")
        # Persist before submitting: a crash or ambiguous UI result must block
        # retries, including the confirmed poster, until the X queue is checked.
        ledger["events"][key] = {**event, "status": "submitting", "requested_at": wanted.isoformat(), "timing": timing}
        model.save_ledger(ledger)
        source = f"milestone-advance:{key}"
        os.environ["TWITTER_SCHEDULE_SOURCE"] = source
        # Recompute under the X account lock, after opening the composer: a
        # concurrent run may have occupied the peak, or the peak may have passed.
        ok = twitter_core.schedule_post(
            tweet, wanted, posts.TWITTER_SESSION, png, not_after=deadline,
            select_slot=lambda current, occupied: choose_slot(target, current, occupied),
        )
        receipts = [e for e in twitter_core.scheduled_entries() if e["source"] == source] if ok else []
        if not ok or len(receipts) != 1:
            ledger["events"][key]["status"] = "uncertain"
            model.save_ledger(ledger)
            raise SystemExit(f"[milestone_schedule] BLOCKED {key}: X scheduling not confirmed; inspect scheduled posts before retrying")
        actual = receipts[0]["at"].astimezone(MADRID)
        ledger["events"][key].update(status="scheduled", scheduled_at=actual.isoformat())
        ledger["events"][key]["timing"]["actual_activity_weight"] = x_active_times.engagement_weight(actual.astimezone(x_active_times.AUDIENCE_TIMEZONE))
        model.save_ledger(ledger)
        reserved.add(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
