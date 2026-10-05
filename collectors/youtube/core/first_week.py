"""First-week daily views of a new music video — one post per day, Day 2..Day 7.

Follows a POSTED first-24h release (core/first_day.py): every main-channel
"Official Music Video" whose exact +24h capture went out gets a 7-day series,
and nothing after the first week (owner 2026-10-01).

Day N = viewCount read at published_at + N*24h minus the one read at
published_at + (N-1)*24h. Windows are anchored on the release instant — never
on YouTube's own day or our NY-midnight collection day.

Each mark is read by the one-off task ``TSM_YouTube_FirstWeek_<id>`` (started
CAPTURE_LEAD early, waits in-process for the exact second — same mechanism as
the +24h capture), or by a daily-run row whose snapshot lands within
CAPTURE_TOLERANCE. A missed window leaves that mark without a reading; since
2026-10-04 (owner's choice: close enough beats n/a) it gets an estimate from
core/estimate.py once a real reading exists after it — Day N and Day N+1 are
then posted like any other figure, to the unit, no public marker (owner: YouTube
figures differ between trackers anyway); `estimated_days()` keeps the trace in
the state. Until then they stay "n/a" and nothing is posted for an unknown day.

Day N is posted right after its capture (text + that day's video card); Day 7
posts the first-week bar chart instead. State:
``tools/json/first_week/<id>.json`` (status active -> done).
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import estimate
from .first_day import (
    CAPTURE_LEAD,
    CAPTURE_TOLERANCE,
    FIRST_DAY,
    HANDLE,
    RELEASES_DIR,
    SNAPSHOTS_DIR,
    TOOLS_JSON_DIR,
    _default_poster,
    _int,
    _iso,
    _read_json,
    _register_one_off_task,
    _release_time_text,
    _repo_path,
    _write_json,
    parse_utc,
    unregister_task,
    upload_kind,
)

FIRST_WEEK_DIR = TOOLS_JSON_DIR / "first_week"
DAYS = 7
# A day's post more than this after its mark is stale: recorded as skipped.
POST_MAX_DELAY = timedelta(hours=12)


def task_name(video_id: str) -> str:
    return f"TSM_YouTube_FirstWeek_{video_id}"


def _state_path(video_id: str) -> Path:
    return FIRST_WEEK_DIR / f"{video_id}.json"


def load_state(video_id: str) -> dict | None:
    data = _read_json(_state_path(video_id))
    return data if isinstance(data, dict) and data.get("video_id") else None


def _save(state: dict) -> None:
    _write_json(_state_path(state["video_id"]), state)


def load_active() -> list[dict]:
    if not FIRST_WEEK_DIR.exists():
        return []
    states = [_read_json(p) for p in sorted(FIRST_WEEK_DIR.glob("*.json"))]
    return [s for s in states if isinstance(s, dict) and s.get("status") == "active"]


def mark_due(state: dict, n: int) -> datetime:
    return parse_utc(state["published_at"]) + n * FIRST_DAY


def next_open_mark(state: dict) -> int | None:
    marks = state.get("marks") or {}
    return next((n for n in range(2, DAYS + 1) if str(n) not in marks), None)


def daily_views(state: dict, *, estimates: bool = False) -> list[int | None]:
    """Views of day 1..7; None when a bounding mark is missing/not yet read.
    estimates=True uses a missed mark's estimate (see estimated_days)."""
    marks = state.get("marks") or {}

    def views(n: int) -> int | None:
        mark = marks.get(str(n)) or {}
        if "views" in mark:
            return _int(mark["views"])
        return _int((mark.get("estimate") or {}).get("views")) if estimates else None

    out: list[int | None] = []
    for n in range(1, DAYS + 1):
        current = views(n)
        if n == 1:
            out.append(current)
        else:
            previous = views(n - 1)
            out.append(current - previous if current is not None and previous is not None else None)
    return out


def estimated_days(state: dict) -> set[int]:
    """Days (1-based) whose estimates=True figure relies on an estimated mark."""
    marks = state.get("marks") or {}
    est = {n for n in range(1, DAYS + 1) if "estimate" in (marks.get(str(n)) or {})}
    return {n for n in range(1, DAYS + 1) if n in est or (n - 1) in est}


def fill_estimates(state: dict) -> None:
    """(Re)estimate every missed mark from all real readings of the video:
    the other marks + the daily-run snapshots. Recomputed each call (a later
    reading tightens it) until a post has shown it: then frozen, so the same
    "~X" is repeated in every later post. Absent until a reading exists after
    the mark."""
    marks = state.get("marks") or {}
    missed = [n for n in range(2, DAYS + 1) if (marks.get(str(n)) or {}).get("missed")]
    if not missed:
        return
    published = parse_utc(state["published_at"])
    # Real readings: daily-run snapshots + first-day/first-week captures on
    # disk + this state's own marks (in memory, may be newer than the file).
    anchors = estimate.csv_anchors(state["video_id"]) + estimate.registry_anchors().get(state["video_id"], [])
    for m in marks.values():
        at, views = parse_utc(m.get("captured_at")), _int(m.get("views"))
        if at is not None and views is not None:
            anchors.append((at, views))
    for n in missed:
        if (marks[str(n)].get("estimate") or {}).get("frozen"):
            continue
        result = estimate.estimate_views_at(mark_due(state, n), anchors, published)
        if result is None:
            marks[str(n)].pop("estimate", None)
        else:
            marks[str(n)]["estimate"] = result


# ---------------------------------------------------------------------------
# Enrollment (from posted first-day releases)
# ---------------------------------------------------------------------------

def enroll_from_releases(now: datetime, *, log: Callable[[str], None] = print) -> list[str]:
    enrolled: list[str] = []
    if not RELEASES_DIR.exists():
        return enrolled
    for path in sorted(RELEASES_DIR.glob("*.json")):
        record = _read_json(path) or {}
        if record.get("status") != "posted":
            continue
        rows_by_upload: dict[str, tuple[dict, dict]] = {}
        for post in record.get("posts") or []:
            if post.get("group") != "video":
                continue
            for row in post.get("rows") or []:
                for video_id in row.get("uploads") or []:
                    rows_by_upload[video_id] = (row, post)
        for member in record.get("members") or []:
            video_id = str(member.get("video_id") or "")
            capture = member.get("capture") or {}
            published = parse_utc(member.get("published_at"))
            if (
                not video_id
                or member.get("result") != "included"
                or member.get("channel") != "main"
                or upload_kind("main", str(member.get("title") or "")) != "Music Video"
                or video_id not in rows_by_upload
                or _int(capture.get("views")) is None
                or published is None
                or now >= published + DAYS * FIRST_DAY + CAPTURE_TOLERANCE
                or _state_path(video_id).exists()
            ):
                continue
            row, post = rows_by_upload[video_id]
            _save({
                "video_id": video_id,
                "title": member.get("title") or video_id,
                "song_title": row.get("title") or member.get("title") or video_id,
                "album": post.get("album"),
                "published_at": _iso(published),
                "thumbnail_url": member.get("thumbnail_url") or "",
                "status": "active",
                "marks": {"1": {**capture, "source": capture.get("source") or "first_day"}},
                "posts": {"1": {"status": "first_day_post"}},
            })
            enrolled.append(video_id)
            log(f"[first_week] {video_id}: suivi 7 jours démarré ({member.get('title')}).")
    return enrolled


# ---------------------------------------------------------------------------
# Captures
# ---------------------------------------------------------------------------

def _record_mark(state: dict, n: int, views: int, at: datetime, source: str) -> None:
    state.setdefault("marks", {})[str(n)] = {
        "views": int(views),
        "captured_at": _iso(at),
        "due_at": _iso(mark_due(state, n)),
        "offset_seconds": int((at - mark_due(state, n)).total_seconds()),
        "source": source,
    }


def _resolve_missed(state: dict, now: datetime, log: Callable[[str], None]) -> None:
    marks = state.setdefault("marks", {})
    for n in range(2, DAYS + 1):
        if str(n) not in marks and now > mark_due(state, n) + CAPTURE_TOLERANCE:
            marks[str(n)] = {"missed": True, "due_at": _iso(mark_due(state, n))}
            log(f"[first_week] {state['video_id']}: fenêtre +{n * 24}h manquée — Day {n} sera estimé "
                "dès qu'une lecture réelle existe après (n/a d'ici là).")


def capture_from_daily_rows(rows: list[dict], snapshot_at: datetime) -> list[str]:
    """Daily-run capture: an active video whose next mark falls within
    CAPTURE_TOLERANCE of this run's snapshot gets this row's total_views."""
    by_id = {str(r.get("video_id") or ""): r for r in rows}
    captured: list[str] = []
    for state in load_active():
        n = next_open_mark(state)
        row = by_id.get(state["video_id"])
        if n is None or row is None or abs(snapshot_at - mark_due(state, n)) > CAPTURE_TOLERANCE:
            continue
        views = _int(row.get("total_views"))
        if views is not None:
            _record_mark(state, n, views, snapshot_at, "daily_run")
            _save(state)
            captured.append(state["video_id"])
    return captured


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------

def build_tweet(state: dict, n: int) -> str:
    from collectors.twitter.albums import album_emoji

    emoji = album_emoji(state.get("album"), fallback="") if state.get("album") else ""
    prefix = "\U0001f3a5 | " + (f"{emoji} " if emoji else "")
    daily = daily_views(state, estimates=True)
    lines: list[str] = []
    for day in range(1, n + 1):
        value, previous = daily[day - 1], daily[day - 2] if day > 1 else None
        if value is None:
            lines.append(f"DAY {day} - n/a")
            continue
        line = f"DAY {day} - {value:,}"
        if previous:
            line += f" ({(value - previous) / previous * 100:+.1f}%)"
        lines.append(line)
    text = f'{prefix}"{state["song_title"]}" music video daily views on YouTube:\n\n' + "\n".join(lines)
    if n == DAYS:
        total = _int((state["marks"].get(str(DAYS)) or {}).get("views"))
        if total is not None:
            text += f"\n\nFirst week: {total:,} views"
    return text


def render_day(state: dict, n: int, out_root: Path = SNAPSHOTS_DIR, *, keep_html: bool = False) -> Path:
    from collectors.comp.tables_image import render_html_to_png
    from collectors.comp.youtube_card import (
        render_youtube_card,
        render_youtube_week_chart,
        slugify,
        write_song_card_png,
    )

    published = parse_utc(state["published_at"])
    day = mark_due(state, n).date().isoformat()
    out_dir = out_root / day[:4] / day[5:7] / day
    base = f"first_week_{slugify(state['song_title'])}_{state['video_id']}_day{n}"
    if n == DAYS:
        total = _int((state["marks"].get(str(DAYS)) or {}).get("views"))
        html_text = render_youtube_week_chart(
            title=state["song_title"],
            subtitle="Official Music Video · daily views from release",
            daily=daily_views(state, estimates=True),
            total_text=f"{total:,} views" if total is not None else "n/a",
            cover_url=state.get("thumbnail_url"),
            handle=HANDLE,
            release_date_text=_release_time_text(published),
        )
        return render_html_to_png(
            html_text, out_dir / f"{base}.png", out_dir / f"{base}.html", width=1000, keep_html=keep_html,
        )
    views = daily_views(state, estimates=True)[n - 1]
    html_text = render_youtube_card(
        title=state["title"],
        stat_label=f"Day {n} · {(n - 1) * 24}h – {n * 24}h",
        stat_value=f"+{views:,} views",
        cover_url=state.get("thumbnail_url"),
        footer_left=HANDLE,
        badge_text=f"DAY {n}",
        release_date_text=_release_time_text(published),
    )
    return write_song_card_png(html_text, out_dir / f"{base}.png", out_dir / f"{base}.html", keep_html=keep_html)


def _acquire_lock(video_id: str) -> Path | None:
    path = FIRST_WEEK_DIR / f"{video_id}.posting"
    FIRST_WEEK_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists() and time.time() - path.stat().st_mtime > 3600:
        path.unlink(missing_ok=True)  # stale (crashed poster)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    os.close(fd)
    return path


def post_ready(
    state: dict,
    now: datetime,
    *,
    poster: Callable[[list[tuple[str, Path]]], bool] | None = None,
    out_root: Path = SNAPSHOTS_DIR,
    log: Callable[[str], None] = print,
) -> str | None:
    """Posts the latest settled day whose figure is known (exact, or estimated
    around a missed mark); older unposted days are skipped (one post per day,
    never a backlog). Returns the status."""
    posts = state.setdefault("posts", {})
    fill_estimates(state)
    daily = daily_views(state, estimates=True)
    # Marks hold either a capture or {"missed": True}: both settle that day.
    todo = [n for n in range(2, DAYS + 1) if str(n) not in posts and str(n) in state["marks"]]
    if not todo:
        return None
    for n in todo:
        mark = state["marks"].get(str(n)) or {}
        latest = n == todo[-1]
        if daily[n - 1] is None:
            if mark.get("missed") and "estimate" not in mark and latest:
                continue  # no reading after the mark yet: decided once one exists
            posts[str(n)] = {"status": "skipped", "reason": "day figure unknown (missed window)"}
        elif not latest or now - mark_due(state, n) > POST_MAX_DELAY:
            posts[str(n)] = {"status": "skipped", "reason": "stale"}
    _save(state)
    n = todo[-1]
    if str(n) in posts:
        return posts[str(n)]["status"]
    if daily[n - 1] is None:
        return None  # missed mark still waiting for a later reading to estimate it
    lock = _acquire_lock(state["video_id"])
    if lock is None:
        log(f"[first_week] {state['video_id']}: post déjà en cours ailleurs.")
        return None
    try:
        tweet = build_tweet(state, n)
        image = render_day(state, n, out_root)
        log(f"[first_week] Day {n}:\n{tweet}\n[first_week] Image: {image}")
        if (poster or _default_poster)([(tweet, image)]):
            posts[str(n)] = {"status": "posted", "tweet": tweet, "image": _repo_path(image), "at": _iso(now)}
            for mark in state["marks"].values():
                if "estimate" in mark:
                    mark["estimate"]["frozen"] = True
            _save(state)
            return "posted"
        log(f"[first_week] {state['video_id']}: échec du post Day {n} — nouvel essai au prochain passage.")
        return "failed"
    finally:
        lock.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Scheduling + entry points
# ---------------------------------------------------------------------------

def _finish_or_schedule(state: dict, now: datetime, log: Callable[[str], None]) -> None:
    n = next_open_mark(state)
    if n is None:
        if all(str(d) in state.get("posts", {}) for d in range(2, DAYS + 1)):
            state["status"] = "done"
            _save(state)
            unregister_task(task_name(state["video_id"]))
            log(f"[first_week] {state['video_id']}: première semaine terminée.")
        return
    _register_one_off_task(
        task_name(state["video_id"]),
        max(mark_due(state, n) - CAPTURE_LEAD, now + timedelta(minutes=1)),
        f"-m collectors.youtube.update_youtube --first-week-tick {state['video_id']}",
        log=log,
    )


def capture_and_post(
    video_id: str,
    now: datetime,
    fetch_stats: Callable[[list[str]], dict[str, dict]],
    *,
    clock: Callable[[], datetime] | None = None,
    poster: Callable[[list[tuple[str, Path]]], bool] | None = None,
    out_root: Path = SNAPSHOTS_DIR,
    log: Callable[[str], None] = print,
) -> int:
    """--first-week-tick <id>: the one-off task fired CAPTURE_LEAD before the
    next +N*24h mark. Waits for the exact second, reads, posts Day N, plans
    the next mark."""
    state = load_state(video_id)
    if state is None or state.get("status") != "active":
        unregister_task(task_name(video_id))
        log(f"[first_week] {video_id}: pas de suivi actif.")
        return 0
    _resolve_missed(state, now, log)
    n = next_open_mark(state)
    if n is not None:
        due = mark_due(state, n)
        if clock is not None and now < due and due - now <= CAPTURE_LEAD + CAPTURE_TOLERANCE:
            log(f"[first_week] {video_id}: attente de +{n * 24}h ({_iso(due)}).")
            while (remaining := (due - clock()).total_seconds()) > 0:
                time.sleep(min(remaining, 5.0))
            now = clock()
        if abs(now - due) <= CAPTURE_TOLERANCE:
            stat = (fetch_stats([video_id]) or {}).get(video_id)
            if stat:
                _record_mark(state, n, int(stat.get("viewCount", 0)), now, "scheduled_task")
                log(f"[first_week] {video_id}: {int(stat.get('viewCount', 0)):,} vues à +{n * 24}h.")
            else:
                log(f"[first_week] {video_id}: stats live indisponibles à +{n * 24}h.")
        _save(state)
    code = 0
    if post_ready(state, now, poster=poster, out_root=out_root, log=log) == "failed":
        code = 1
    _finish_or_schedule(state, now, log)
    return code


def sync(
    now: datetime,
    *,
    poster: Callable[[list[tuple[str, Path]]], bool] | None = None,
    log: Callable[[str], None] = print,
) -> None:
    """Idempotent: enroll freshly posted music videos, settle missed marks,
    post a captured day that has not gone out yet, (re)plan the next task.
    Called after every first-day post tick and at the end of the daily run."""
    enroll_from_releases(now, log=log)
    for state in load_active():
        _resolve_missed(state, now, log)
        _save(state)
        post_ready(state, now, poster=poster, log=log)
        _finish_or_schedule(state, now, log)


def status_lines(now: datetime) -> list[str]:
    states = load_active()
    if not states:
        return ["[first_week] Aucun suivi première semaine en cours."]
    lines: list[str] = []
    for state in states:
        lines.append(f"{state['video_id']} {state['song_title']} — publié {state['published_at']}")
        fill_estimates(state)
        daily = daily_views(state, estimates=True)
        est_days = estimated_days(state)
        for n in range(1, DAYS + 1):
            mark = state["marks"].get(str(n)) or {}
            post = (state.get("posts") or {}).get(str(n), {}).get("status", "")
            if "views" in mark:
                value = daily[n - 1]
                shown = "n/a" if value is None else f"{value:,}" + (" (estimé)" if n in est_days else "")
                lines.append(f"  Day {n}: {shown} ({mark['views']:,} cumulées) {post}")
            elif mark.get("estimate"):
                lines.append(f"  Day {n}: manqué — estimé {mark['estimate']['views']:,} cumulées "
                             f"(entre {mark['estimate']['low']:,} et {mark['estimate']['high']:,}) {post}")
            elif mark.get("missed"):
                lines.append(f"  Day {n}: manqué {post}")
            else:
                lines.append(f"  Day {n}: prévu {_iso(mark_due(state, n))}")
    return lines
