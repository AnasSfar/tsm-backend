#!/usr/bin/env python3
"""release_watch.py — catch a release on iTunes the second it shows up.

Why (2026-09-25, The Encore at 00:00 ET = 06:00 Paris, owner: "on doit retry
chaque seconde jusqu'à avoir la data"): the hourly chain fetches the feeds
once, at HH:00:01. If Apple adds the songs 30 s later, the debut posts wait
for the next hour. This watcher polls one key-country Top Songs feed per
second (US every other second) plus the lookup API every 15 s, and the moment
a new track appears in a key chart:

  1. EXPRESS post: fetches the 5 key countries' Top Songs + Top Albums (10
     requests, a few seconds) into collectors/apple_music/tools/json/express/
     and runs post_new_release_progression's iTunes posting on them
     (run_platform(express=...)): debuts, biggest news first, no worldwide
     line (we don't have the 168 countries yet — never a partial count).
  2. In parallel, the FULL iTunes collection (run_itunes.py) for this hour,
     retried while the hourly run holds its lock (exit 75), then the normal
     post step — which only posts what moved since the express posts
     (shared state, serialized by platform_lock).

Throttling: the legacy RSS host answers bursts with 403 and is shared with the
real collector, so a 403/429 backs the poller off (15 s -> 120 s) instead of
hammering it. Exits after triggering once, at --until, or when every
candidate is already known as released.

No separate process or task (owner 2026-09-25): run_itunes.py calls
run_if_release_due() at the end of every hourly run. A release goes live at
00:00 America/New_York on its catalog release date, so the watch window is
that moment -10 min -> +2 h; the run that ends within 70 min before the window
(the 05:00 run for a 06:00 Paris drop) keeps going — waits for the window and
polls — every other run returns after a date comparison. Its collection lock
is released first, so the next hourly runs keep collecting normally; a
single-instance OS lock (tools/locks/release_watch.lock) keeps one watcher.

Usage:
    (automatic, from run_itunes.py)
    python collectors/itunes/release_watch.py --until 08:30 [--interval 1] [--dry-run] [--watch-ids ...]
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
AM_DIR = REPO_ROOT / "collectors" / "apple_music"
for p in (str(REPO_ROOT), str(AM_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import post_new_release_progression as prog  # noqa: E402
from collectors.itunes.core.config import CHART_LIMIT, RSS_ALBUMS_PATH, RSS_BASE, RSS_SONGS_PATH  # noqa: E402
from collectors.itunes.core.http import build_session  # noqa: E402
from collectors.itunes.core.rss import _entries, _fetch, feed_url, parse_albums, parse_songs  # noqa: E402

PARIS = ZoneInfo("Europe/Paris")
EXPRESS_DIR = AM_DIR / "tools" / "json" / "express"
SONG_FIELDS = ["date", "scraped_at", "country", "chart_type", "song_name", "apple_music_id", "rank", "previous_rank",
               "image_url", "url", "artist_name", "album_name", "genre_names", "release_date", "explicitness"]
ALBUM_FIELDS = ["date", "scraped_at", "country", "chart_type", "album_name", "apple_music_id", "rank",
                "previous_rank", "image_url", "url", "artist_name", "genre_names", "release_date"]
PY = sys.executable


def log(msg: str) -> None:
    print(f"[release_watch {datetime.now(PARIS).strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--auto", action="store_true",
                    help="Hourly-chain mode: watch only around a candidate's 00:00 New York release, else exit.")
    ap.add_argument("--until", default="08:30", help="Paris time HH:MM to give up (manual mode, default 08:30).")
    ap.add_argument("--interval", type=float, default=1.0, help="Seconds between feed polls (default 1).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Detect and fetch the express rows, but post nothing and launch nothing.")
    ap.add_argument("--watch-ids", default="",
                    help="Test only: comma-separated Apple ids to treat as the release (e.g. an old song).")
    return ap.parse_args(argv)


def _poll_order() -> list[str]:
    others = [c for c in prog.KEY_COUNTRIES if c != "us"]
    order: list[str] = []
    for c in others:
        order += ["us", c]
    return order or ["us"]


def _hit(targets: list[dict], rows: list[dict]) -> list[str]:
    return sorted({t["title"] for t in targets for r in rows if prog._row_matches(t, r)})


def _storefronts() -> list[str]:
    """Every iTunes storefront (same discovery as charts.py, ~168), key markets
    first. Resolved BEFORE the release so detection doesn't wait for it."""
    try:
        from collectors.itunes.core.storefronts import resolve_storefronts

        found = [c.lower() for c in resolve_storefronts()]
    except Exception as exc:
        log(f"storefront discovery failed ({exc}) — key markets only")
        found = []
    return prog.KEY_COUNTRIES + [c for c in found if c not in prog.KEY_COUNTRIES]


def _express_fetch(scraped_at: str, storefronts: list[str], albums: bool = False) -> dict[str, Path]:
    """Top Songs of EVERY storefront (owner 2026-09-25: the debut card lists all
    the regions, ~1 min at 3 parallel requests — the legacy RSS host 403s
    above that), same parsing/filtering as charts.py, real CSV schema, into
    EXPRESS_DIR. Albums are left to the full collection that runs right after
    (their debut card follows the song debuts, with all regions too): fetching
    them here would delay the first song post by another minute. A storefront
    that fails is skipped (it shows up in the next full snapshot); US/UK must
    succeed."""
    session = build_session(timeout=10)

    def one(job: tuple[str, str]):
        kind, cc = job
        try:
            if kind == "album":
                return kind, cc, parse_albums(_fetch(session, cc, RSS_ALBUMS_PATH))
            return kind, cc, parse_songs(_fetch(session, cc, RSS_SONGS_PATH))
        except Exception as exc:
            if cc in ("us", "gb"):
                raise
            log(f"express: {kind} {cc} skipped ({type(exc).__name__})")
            return kind, cc, None

    t0 = time.monotonic()
    jobs = [("song", cc) for cc in storefronts] + ([("album", cc) for cc in storefronts] if albums else [])
    with ThreadPoolExecutor(max_workers=3) as pool:
        fetched = [r for r in pool.map(one, jobs) if r[2] is not None]
    results = [(cc, rows) for kind, cc, rows in fetched if kind == "song"]
    album_results = [(cc, rows) for kind, cc, rows in fetched if kind == "album"]
    EXPRESS_DIR.mkdir(parents=True, exist_ok=True)
    paths = {"song": EXPRESS_DIR / "itunes_express_songs.csv", "album": EXPRESS_DIR / "itunes_express_albums.csv"}
    with paths["song"].open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SONG_FIELDS, extrasaction="ignore")
        w.writeheader()
        for cc, rows in results:
            for row in rows:
                w.writerow({**row, "date": scraped_at[:10], "scraped_at": scraped_at, "country": cc,
                            "chart_type": "itunes_country"})
    with paths["album"].open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=ALBUM_FIELDS, extrasaction="ignore")
        w.writeheader()
        for cc, rows in album_results:
            for row in rows:
                w.writerow({**row, "date": scraped_at[:10], "scraped_at": scraped_at, "country": cc,
                            "chart_type": "itunes_country_albums"})
    log(f"express rows: {len(results)}/{len(storefronts)} storefronts"
        + (f" + {len(album_results)} album charts" if albums else "") + f" in {time.monotonic() - t0:.0f}s")
    return paths


def _full_collection() -> int:
    """run_itunes.py for this hour; waits while the hourly run holds the lock
    (75) and retries a failed run (it already retried each step inside)."""
    deadline = time.monotonic() + 30 * 60
    failures = 0
    while True:
        code = subprocess.run([PY, "-u", str(HERE / "run_itunes.py")], cwd=REPO_ROOT).returncode
        if code == 0 or time.monotonic() > deadline:
            return code
        if code == 75:
            log("hourly iTunes run still going — retrying the full collection in 10 s")
            time.sleep(10)
            continue
        failures += 1
        if failures >= 3:
            return code
        log(f"full collection failed (code {code}) — retrying in 30 s ({failures}/3)")
        time.sleep(30)


NY = ZoneInfo("America/New_York")
WINDOW_BEFORE_MIN, WINDOW_AFTER_H, START_AHEAD_MIN = 10, 2, 70
LOCK_PATH = HERE / "tools" / "locks" / "release_watch.lock"


def _release_window(track: dict) -> tuple[datetime, datetime]:
    """00:00 New York on the catalog release DATE (Taylor's global drops), in Paris."""
    day = track["catalog_release"].date()
    drop = datetime(day.year, day.month, day.day, tzinfo=NY).astimezone(PARIS)
    return drop - timedelta(minutes=WINDOW_BEFORE_MIN), drop + timedelta(hours=WINDOW_AFTER_H)


def _single_instance():
    """OS lock held for the whole process (released by the OS if it dies).
    None = another watcher is already running."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def preflight(targets: list[dict]) -> None:
    """Right before watching (05:50 for the Encore): checks what the posts will
    need and sends ONE phone notification — "ready" (low priority) or the list
    of problems (high). Never raises."""
    import shutil

    problems: list[str] = []
    now_ts = time.time()
    try:
        from collectors.spotify.core.data_paths import apple_music_daily_csv, itunes_daily_csv

        today = datetime.now(PARIS).strftime("%Y-%m-%d")
        for label, path in (("iTunes", itunes_daily_csv(today, "itunes_top_songs.csv")),
                            ("Apple Music", apple_music_daily_csv(today, "apple_music_country_charts.csv"))):
            age = (now_ts - path.stat().st_mtime) / 60 if path.exists() else None
            if age is None or age > 90:
                problems.append(f"last {label} collection {'missing' if age is None else f'{age:.0f} min old'}")
    except Exception as exc:
        problems.append(f"collection check failed: {exc}")
    try:
        session = build_session(retry_total=0, timeout=10)
        code = session.get(feed_url("us", RSS_SONGS_PATH)).status_code
        if code >= 400:
            problems.append(f"iTunes US feed answers HTTP {code}")
    except Exception as exc:
        problems.append(f"iTunes US feed unreachable: {type(exc).__name__}")
    ids = set().union(*(t["apple_ids"] for t in targets)) if targets else set()
    if ids and prog._apple_availability(ids, ["us"]) is None:
        problems.append("Apple lookup API unreachable")
    free_gb = shutil.disk_usage(REPO_ROOT).free / 1e9
    if free_gb < 2:
        problems.append(f"only {free_gb:.1f} GB free on disk")
    if not (prog.TWITTER_SESSION.parent / "chrome_profile").exists():
        problems.append("X chrome profile folder missing")

    titles = ", ".join(t["title"] for t in targets)
    if problems:
        prog.alert("Release watch preflight — PROBLEMS: " + "; ".join(problems) + f" (watching {titles})",
                   title="Release watch", priority="high")
    else:
        prog.alert(f"Release watch armed: collections fresh, feeds + Apple API up, {free_gb:.0f} GB free. "
                   f"Watching {titles} every second.", title="Release watch", priority="low", tags="white_check_mark")
    log("preflight: " + ("; ".join(problems) if problems else "all good"))


class _Args:
    platform = "itunes"
    scraped_at = None
    window_hours = prog.DEBUT_WINDOW_HOURS
    no_post = False
    dry_run = False


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    now = datetime.now(PARIS)
    until = now.replace(hour=int(args.until[:2]), minute=int(args.until[3:5]), second=0, microsecond=0)

    if args.auto:
        state = prog.load_state("itunes")
        pending = [t for t in prog.candidate_tracks(now, prog.DEBUT_WINDOW_HOURS)
                   if not state.get(f"__released_at|{t['key']}")]
        windows = [w for w in (_release_window(t) for t in pending)
                   if w[1] > now and w[0] - timedelta(minutes=START_AHEAD_MIN) <= now]
        if not windows:
            targets = _follow_targets(now)
            if not targets:
                return  # silent: nothing due
            lock = _single_instance()
            if lock is None:
                return  # a follower from the previous hour is still finishing
            # This hour's normal post first (full snapshot, album card): the
            # bat's own post step only runs once the follow ends (~HH:58).
            subprocess.run([PY, "-u", str(AM_DIR / "post_new_release_progression.py"), "--platform", "itunes"],
                           cwd=REPO_ROOT)
            follow(None, _follow_until(now, targets))
            return
        lock = _single_instance()
        if lock is None:
            log("another watcher is already running — exit")
            return
        start, until = min(w[0] for w in windows), max(w[1] for w in windows)
        if now < start:
            log(f"release window {start:%H:%M}-{until:%H:%M} — sleeping until {start:%H:%M}")
            time.sleep((start - now).total_seconds())
        now = datetime.now(PARIS)

    if args.watch_ids:
        targets = [{"title": f"test id {i}", "key": f"test|{i}", "keys": set(), "apple_ids": {i}}
                   for i in args.watch_ids.split(",") if i]
    else:
        state = prog.load_state("itunes")
        targets = [t for t in prog.candidate_tracks(now, prog.DEBUT_WINDOW_HOURS)
                   if not state.get(f"__released_at|{t['key']}")]
    if not targets:
        log("nothing to watch (no unreleased candidate) — exit")
        return
    ids = set().union(*(t["apple_ids"] for t in targets))
    if args.auto and not args.dry_run:
        preflight(targets)
    log(f"watching {', '.join(t['title'] for t in targets)} until {until:%H:%M} "
        f"(1 feed / {args.interval:g}s, lookup / 15s)")

    storefronts = _storefronts()  # before the drop: detection must not wait for it
    log(f"{len(storefronts)} storefronts ready for the express fetch")
    session = build_session(retry_total=0, timeout=5)
    order = _poll_order()
    i, backoff, lookup_at, announced = 0, 0.0, 0.0, False
    hits: list[str] = []
    while datetime.now(PARIS) < until:
        t0 = time.monotonic()
        if ids and not announced and t0 - lookup_at >= 15:
            lookup_at = t0
            avail = prog._apple_availability(ids, ["us"])
            if avail and any(avail.values()):
                announced = True
                log("Apple lookup says OUT (streamable/purchasable) — waiting for the charts")
        cc = order[i % len(order)]
        i += 1
        try:
            resp = session.get(feed_url(cc, RSS_SONGS_PATH))
            if resp.status_code in (403, 429):
                backoff = min(max(backoff * 2, 15.0), 120.0)
                log(f"HTTP {resp.status_code} on {cc} — backing off {backoff:.0f}s")
                time.sleep(backoff)
                continue
            backoff = 0.0
            if resp.status_code < 400:
                hits = _hit(targets, parse_songs(_entries(resp.json())))
                if hits:
                    log(f"DETECTED on {cc}: {', '.join(hits)}")
                    break
        except Exception as exc:
            log(f"poll {cc} failed: {type(exc).__name__}: {exc}")
        time.sleep(max(0.0, args.interval - (time.monotonic() - t0)))
    else:
        log("gave up at --until without detection (the hourly chain keeps running)")
        return

    detected_at = datetime.now(PARIS).replace(microsecond=0)
    scraped_at = detected_at.isoformat()
    express = None
    for attempt in range(1, 6):  # "on réessaie toujours": the debut must not wait an hour
        try:
            express = _express_fetch(scraped_at, storefronts)
            break
        except Exception as exc:
            log(f"express fetch attempt {attempt}/5 failed: {type(exc).__name__}: {exc}")
            time.sleep(2 * attempt)
    if express is None:
        prog.alert("Express fetch kept failing — falling back to the full collection for the debut posts.")
    if args.dry_run or args.watch_ids:
        log("dry-run / test ids: express rows written, nothing posted, nothing launched")
        return

    with ThreadPoolExecutor(max_workers=1) as pool:
        full = pool.submit(_full_collection)  # 168 countries, in parallel with the express posts
        try:
            if express is None:
                raise RuntimeError("no express rows (see previous alert)")
            with prog.platform_lock("itunes"):
                prog.run_platform("itunes", _Args(), detected_at, detected_at.strftime("%Y-%m-%d"), express=express)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            prog.alert(f"Express iTunes posts crashed: {type(exc).__name__}: {exc}")
        code = full.result()
    log(f"full iTunes collection finished (code {code})")
    if code == 0:
        subprocess.run([PY, "-u", str(AM_DIR / "post_new_release_progression.py"), "--platform", "itunes"],
                       cwd=REPO_ROOT)
    log("done")
    now = datetime.now(PARIS)
    targets = _follow_targets(now)
    if targets and not (args.dry_run or args.watch_ids):
        follow(storefronts, _follow_until(now, targets))


FOLLOW_HOURS = float(os.getenv("ITUNES_FOLLOW_HOURS", "24"))
FOLLOW_INTERVAL = float(os.getenv("ITUNES_FOLLOW_INTERVAL", "2"))
ALBUM_POLL_EVERY = 4  # 1 key Top Albums read every 4 ticks (the 403 budget is shared)


def _follow_targets(now: datetime) -> list[dict]:
    """Candidates already out for less than FOLLOW_HOURS (+ their album)."""
    state = prog.load_state("itunes")
    out = []
    for t in prog.candidate_tracks(now, prog.DEBUT_WINDOW_HOURS):
        released = prog._parse_release_date(state.get(f"__released_at|{t['key']}") or "")
        if released and now - released < timedelta(hours=FOLLOW_HOURS):
            out.append({**t, "released_at": released})
    return out


def _worth_express(items: list[dict], ranks: dict[tuple[str, str], int], now: datetime) -> list[str]:
    """Key-country moves post_new_release_progression would post NOW, vs the
    LAST POSTED state (same rules as run_platform: prog.post_due). Drops and
    rank wobbles never trigger a fetch."""
    state = prog.load_state("itunes")
    why = []
    for t in items:
        prefix = "itunes_albums_" if t.get("is_album") else "itunes_"
        placements = []
        for cc in prog.KEY_COUNTRIES:
            rank = ranks.get((t["key"], cc))
            if rank is None:
                continue
            prev = state.get(f"{t['key']}|{prefix}{cc}") or {}
            prev_rank = prev.get("rank")
            prior_peak = int(prev.get("peak") or prev_rank) if prev_rank is not None else None
            placements.append({
                "region": cc, "rank": rank, "key": True, "is_new": prev_rank is None,
                "moved": prev_rank != rank, "delta": (prev_rank - rank) if prev_rank is not None else None,
                "new_peak": prior_peak is not None and rank < prior_peak, "re_peak": False,
            })
        moved = [p for p in placements if p["moved"]]
        if moved and prog.post_due(t, moved, state, now) is None:
            why.append(f"{t['title']}: " + ", ".join(
                f"{'debut' if p['is_new'] else 'peak' if p['new_peak'] else 'up'} #{p['rank']} {p['region']}"
                for p in moved))
    return why


def follow(storefronts: list[str] | None, until: datetime, interval: float = FOLLOW_INTERVAL) -> None:
    """After the drop (owner 2026-09-25: the iTunes chart is LIVE, it moves
    every minute, not every hour): polls the key-country Top Songs feeds (and a
    key Top Albums feed every ALBUM_POLL_EVERY ticks) and, the moment a move
    the posting rules would post right now shows up, re-runs the express fetch
    of every storefront + the express posting.

    A rank only counts once two consecutive reads of that feed agree: Apple's
    RSS origin serves different versions of the same feed seconds apart
    (Patient Zero US #1 / absent / #1 on 2026-09-25). Ends at `until`; the
    next hourly run starts the next follow (single-instance lock)."""
    targets = _follow_targets(datetime.now(PARIS))
    if not targets:
        return
    albums = prog.album_items(targets)
    storefronts = storefronts or _storefronts()
    log(f"following {', '.join(t['title'] for t in targets + albums)} until {until:%H:%M} "
        f"(1 key feed / {interval:g}s)")
    session = build_session(retry_total=0, timeout=5)
    order = _poll_order()
    raw: dict[tuple[str, str], dict] = {}   # last read per (kind, cc)
    ranks: dict[tuple[str, str], int] = {}  # confirmed ranks per (item key, cc)
    seen: set[str] = set()
    i, backoff, last_why, last_try = 0, 0.0, None, 0.0
    while datetime.now(PARIS) < until:
        t0 = time.monotonic()
        kind = "album" if i % ALBUM_POLL_EVERY == ALBUM_POLL_EVERY - 1 else "song"
        cc = prog.KEY_COUNTRIES[(i // ALBUM_POLL_EVERY) % len(prog.KEY_COUNTRIES)] if kind == "album" \
            else order[i % len(order)]
        i += 1
        path = RSS_ALBUMS_PATH if kind == "album" else RSS_SONGS_PATH
        items = albums if kind == "album" else targets
        try:
            resp = session.get(feed_url(cc, path))
            if resp.status_code in (403, 429):
                backoff = min(max(backoff * 2, 15.0), 120.0)
                log(f"HTTP {resp.status_code} on {cc} — backing off {backoff:.0f}s")
                time.sleep(backoff)
                continue
            backoff = 0.0
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}")
            rows = (parse_albums if kind == "album" else parse_songs)(_entries(resp.json()))
            read = {t["key"]: prog._best_match(t, rows)[1] for t in items}
            if raw.get((kind, cc)) == read:  # confirmed by two reads in a row
                for key, rank in read.items():
                    if rank is None:
                        ranks.pop((key, cc), None)
                    else:
                        ranks[(key, cc)] = rank
                if kind == "song":
                    seen.add(cc)
            raw[(kind, cc)] = read
        except Exception as exc:
            log(f"poll {kind} {cc} failed: {type(exc).__name__}: {exc}")
        if seen >= set(prog.KEY_COUNTRIES):
            now = datetime.now(PARIS).replace(microsecond=0)
            why = _worth_express(targets + albums, ranks, now)
            if why and (why != last_why or time.monotonic() - last_try > 120):
                last_why, last_try = why, time.monotonic()  # same move again = the post failed: retry
                log("MOVE: " + "; ".join(why))
                try:
                    with_albums = any(w.startswith(tuple(a["title"] + ":" for a in albums)) for w in why)
                    express = _express_fetch(now.isoformat(), storefronts, albums=with_albums)
                    with prog.platform_lock("itunes"):
                        prog.run_platform("itunes", _Args(), now, now.strftime("%Y-%m-%d"), express=express)
                except Exception as exc:
                    import traceback

                    traceback.print_exc()
                    prog.alert(f"iTunes follow post crashed: {type(exc).__name__}: {exc}")
            elif not why:
                last_why = None
        time.sleep(max(0.0, interval - (time.monotonic() - t0)))
    log("follow: end of this hour's watch")


def _follow_until(now: datetime, targets: list[dict]) -> datetime:
    """Just before the next hourly run (it takes over), capped at FOLLOW_HOURS."""
    hour_end = now.replace(minute=58, second=30, microsecond=0)
    if hour_end <= now:
        hour_end += timedelta(hours=1)
    cap = max(t["released_at"] for t in targets) + timedelta(hours=FOLLOW_HOURS)
    return min(hour_end, cap)


def run_if_release_due() -> None:
    """Called at the end of every run_itunes.py (after its lock is released):
    a date comparison, nothing else, unless a catalog release is due within
    ~70 min — then this same process waits for the window and polls."""
    try:
        main(["--auto"])
    except Exception as exc:
        import traceback

        traceback.print_exc()
        prog.alert(f"Release watch crashed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
