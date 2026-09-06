from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Callable

from core.data_paths import db_file, update_streams_dir
from core.swift_top_gate import check_swift_top_gate, mark_swift_top_done
from core.retention import cleanup_generated_artifacts
from git_ops import git_commit_and_push
import generate_album_update_image
import post_best_day_since_twitter
import score_album_update
from post_debut_releases import post_debut_releases as run_debut_release_posts


# Album scrape-priority hint (see update_streams.build_album_post_priority_track_ids):
# these albums' tracks are scraped first so their cards + the top-score cards can be
# built early. It no longer drives any early POST (removed 2026-09-03).
ALBUM_UPDATE_TARGETS = (
    "The Life of a Showgirl",
    "reputation",
    "THE TORTURED POETS DEPARTMENT",
)
# Forced right after the two best-scoring albums in the daily post queue
# (_album_post_queue), if they are not already in that top 2.
PRIMARY_ALBUM_UPDATE_TARGETS = (
    "The Life of a Showgirl",
    "THE TORTURED POETS DEPARTMENT",
)
# The lowest-scoring albums (excluding the guaranteed top 2 / forced primary
# targets) skip their card entirely for the day (decision 2026-09-04).
BOTTOM_ALBUMS_SKIPPED = 2
FINALIZE_POST_RETRY_ATTEMPTS = max(1, int(os.getenv("FINALIZE_POST_RETRY_ATTEMPTS", "3")))
FINALIZE_POST_RETRY_SLEEP_SECONDS = max(0, int(os.getenv("FINALIZE_POST_RETRY_SLEEP_SECONDS", "60")))


def _subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # Priorite de post X par defaut pour les etapes streams finalize. Les tweets
    # charts (TWITTER_POST_PRIORITY=1, pose par run_all_charts) passent devant ;
    # les posts "priority early" pendant la collecte descendent a 0. Voir
    # core.twitter._twitter_account_slot.
    env.setdefault("TWITTER_POST_PRIORITY", "3")
    if extra:
        env.update(extra)
    return env


def _run_subprocess(cmd: list[str], **kwargs):
    env = kwargs.pop("env", None)
    return subprocess.run(cmd, env=_subprocess_env(env), **kwargs)


class StepTimer:
    def __init__(self, label: str) -> None:
        self.label = label
        self.started_at = time.perf_counter()
        self.rows: list[tuple[str, float]] = []
        self._lock = threading.Lock()

    @contextmanager
    def step(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            with self._lock:
                self.rows.append((name, elapsed))
            print(f"[timer] {name}: {elapsed:.1f}s")

    def add(self, name: str, elapsed: float) -> None:
        with self._lock:
            self.rows.append((name, elapsed))
        print(f"[timer] {name}: {elapsed:.1f}s")

    def summary(self) -> None:
        total = time.perf_counter() - self.started_at
        with self._lock:
            rows = sorted(self.rows, key=lambda row: row[1], reverse=True)
        print()
        print(f"[timer] {self.label} summary ({total:.1f}s total)")
        for name, elapsed in rows:
            print(f"[timer]   {name:<28} {elapsed:>6.1f}s")


@dataclass
class FinalizeContext:
    script_dir: Path
    repo_root: Path
    stats_date: str
    summary: dict
    no_post_mode: bool
    debug_daily_mode: bool
    local_test_mode: bool
    post_spacing_seconds: int
    log_mode: str
    artist_thread: Any
    artist_result: list
    export_web_data: Callable[..., None]
    update_artist_metadata: Callable[..., dict]
    album_tracks_done_for: Callable[[str, str], bool]
    all_album_tracks_done: Callable[[str], bool]
    load_album_sections_flat: Callable[[], list[dict]]
    extract_track_id: Callable[[str | None], str | None]
    load_history_track_ids_for_date: Callable[[str], set[str]]
    find_biggest_album_gainer_for_spotlight: Callable[..., dict | None]
    posted_album_updates: set[str]
    initial_post_state: dict[str, float]
    throwback_mode: bool = False
    throwback_action: str | None = None
    throwback_event: str | None = None
    throwback_label: str | None = None
    throwback_force: bool = False
    test_mode: bool = False
    posted_best_day_since_tracks: set[str] = field(default_factory=set)


class PartialWebExporter:
    """Re-export web/site data after each retry round while the streams
    collection is still running, so already-updated tracks show current
    numbers on the site without waiting for every pending track to finish."""

    def __init__(
        self,
        *,
        stats_date: str,
        export_web_data: Callable[..., None],
        enabled: bool,
    ) -> None:
        self.stats_date = stats_date
        self.export_web_data = export_web_data
        self.enabled = enabled

    def export_if_updated(self, summary: dict) -> None:
        if not self.enabled or not summary.get("updated_this_run"):
            return
        print(f"Partial web export after retry round (stats_date={self.stats_date})...")
        try:
            self.export_web_data(stats_date=self.stats_date)
        except Exception as exc:
            print(f"Partial web export failed: {exc}")


class SharedWebExportGate:
    """Serialize early web exports and skip repeats when source data is unchanged."""

    def __init__(
        self,
        *,
        export_web_data: Callable[..., None],
        stats_date: str,
        history_path: Path | None = None,
    ) -> None:
        self.export_web_data = export_web_data
        self.stats_date = stats_date
        self.history_path = history_path or db_file("streams_history.csv")
        self.daily_history_path = update_streams_dir(stats_date) / "streams_history.csv"
        self._lock = threading.Lock()
        self._last_signature: tuple[tuple[str, int, int], ...] | None = None

    def _source_signature(self) -> tuple[tuple[str, int, int], ...]:
        signature: list[tuple[str, int, int]] = []
        for path in (self.history_path, self.daily_history_path):
            try:
                stat = path.stat()
            except FileNotFoundError:
                signature.append((str(path), -1, -1))
                continue
            signature.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(signature)

    def export_partial(self, **kwargs) -> None:
        """Export the current site state once per source-data version.

        Early posts need a fresh local site export, but they do not need to
        upload to R2 while collection may still be incomplete.
        """
        stats_date = kwargs.pop("stats_date", self.stats_date)
        signature = self._source_signature()
        with self._lock:
            if signature == self._last_signature:
                return
            self.export_web_data(stats_date=stats_date, allow_r2=False, **kwargs)
            self._last_signature = signature


class ReadyEraRecapPoster:
    """Post the dedicated per-era best-day recap card as soon as an era's album
    is complete during the streams run.

    (Album-level best-day records are no longer posted as their own card - they
    are folded into the first line of each album's daily update card, handled by
    the finalize album update cards / sweep.)"""

    def __init__(
        self,
        *,
        script_dir: Path,
        stats_date: str,
        export_web_data: Callable[..., None],
        album_tracks_done_for: Callable[[str, str], bool],
        spacing_seconds: int,
        log_mode: str,
        enabled: bool,
        no_post_mode: bool,
        target_albums: list[str] | tuple[str, ...],
        priority_ready: Callable[[], bool] | None = None,
        on_post: Callable[[], None] | None = None,
    ) -> None:
        self.script_dir = script_dir
        self.stats_date = stats_date
        self.on_post = on_post
        self.export_web_data = export_web_data
        self.album_tracks_done_for = album_tracks_done_for
        self.spacing_seconds = spacing_seconds
        self.log_mode = log_mode
        self.enabled = enabled
        self.no_post_mode = no_post_mode
        self.target_albums = tuple(dict.fromkeys(target_albums))
        self.priority_ready = priority_ready or (lambda: True)
        self._checked: set[str] = set()
        self._era_recap_checked: set[str] = set()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._post_state = {"posted_count": 0, "last_post_at": 0.0}

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="ready-era-recap-posts", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def post_state(self) -> dict[str, float]:
        with self._lock:
            return dict(self._post_state)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._post_newly_ready_era_recap():
                continue
            if self._done():
                return
            self._stop.wait(1.0)

    def _done(self) -> bool:
        with self._lock:
            return len(self._checked) >= len(self.target_albums)

    def _post_newly_ready_era_recap(self) -> bool:
        for album in self.target_albums:
            with self._lock:
                if album in self._checked:
                    continue
            if not self.album_tracks_done_for(album, self.stats_date):
                continue
            if not self.priority_ready():
                return False
            with self._lock:
                self._checked.add(album)

            # Dedicated per-era best-day recap card (>= 5 post-eligible best-day
            # songs of the era), before this era's album card. Own cap of 2,
            # does not spend the song early-post quota. Fire once per era.
            era = post_best_day_since_twitter._album_key(album)
            if not era or era in self._era_recap_checked:
                return True
            self._era_recap_checked.add(era)

            print(f"Era best-day recap check ready during streams run: {album} ({era})")
            print("Exporting current web data before early era best-day recap post...")
            self.export_web_data(stats_date=self.stats_date)

            best_day_script = self.script_dir / "tools" / "scripts" / "post_best_day_since_twitter.py"
            recap_cmd = [
                sys.executable, str(best_day_script), self.stats_date,
                "--only-era-recap", album,
            ]
            if self.no_post_mode:
                recap_cmd.append("--no-post")
            _wait_before_post(
                label=f"early era best-day recap ({album})",
                should_post=not self.no_post_mode,
                state=self._post_state,
                spacing_seconds=self.spacing_seconds,
                log_mode=self.log_mode,
            )
            recap_result = _run_subprocess(recap_cmd, check=False, env={"TWITTER_POST_PRIORITY": "0"})
            if recap_result.returncode == 0:
                print(f"Era best-day recap posted early during streams run: {album} ({era})")
                _mark_post_done(should_post=not self.no_post_mode, state=self._post_state)
                _run_on_post_callback(self.on_post)
            elif recap_result.returncode != 3:
                print(f"Early era best-day recap check failed for {album} (exit {recap_result.returncode}); skipping.")
            return True
        return False


class ReadyDebutReleasePoster:
    """Post debut release cards as soon as all same-day release tracks are ready."""

    def __init__(
        self,
        *,
        stats_date: str,
        track_ids: set[str],
        load_history_track_ids_for_date: Callable[[str], set[str]],
        spacing_seconds: int,
        log_mode: str,
        enabled: bool,
        no_post_mode: bool,
    ) -> None:
        self.stats_date = stats_date
        self.track_ids = set(track_ids)
        self.load_history_track_ids_for_date = load_history_track_ids_for_date
        self.spacing_seconds = spacing_seconds
        self.log_mode = log_mode
        self.enabled = enabled and bool(self.track_ids)
        self.no_post_mode = no_post_mode
        self._posted = False
        self._stop = threading.Event()
        self._finished = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._post_state = {"posted_count": 0, "last_post_at": 0.0}
        if not self.enabled:
            self._finished.set()

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="ready-debut-release-posts", daemon=True)
        self._thread.start()

    def stop(self) -> dict[str, float]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self._finished.set()
        with self._lock:
            return dict(self._post_state)

    def is_done(self) -> bool:
        return self._finished.is_set()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                done_ids = self.load_history_track_ids_for_date(self.stats_date)
                missing = self.track_ids - done_ids
                if missing:
                    self._stop.wait(2.0)
                    continue

                with self._lock:
                    if self._posted:
                        return
                    self._posted = True

                _wait_before_post(
                    label="early debut release posts",
                    should_post=not self.no_post_mode,
                    state=self._post_state,
                    spacing_seconds=self.spacing_seconds,
                    log_mode=self.log_mode,
                )
                result = run_debut_release_posts(self.stats_date, no_post=self.no_post_mode, priority=0)
                if result == 0:
                    _mark_post_done(should_post=not self.no_post_mode, state=self._post_state)
                    print("[debut] Posted early during streams run.")
                else:
                    print(f"[debut] Early debut release post failed (exit {result}); finalization will retry if needed.")
                return
        finally:
            self._finished.set()


class ReadyBestDaySincePoster:
    """Post individual best-day-since track records as soon as they update,
    without waiting for the full streams collection to finish."""

    def __init__(
        self,
        *,
        script_dir: Path,
        stats_date: str,
        track_ids: list[str],
        export_web_data: Callable[..., None],
        load_history_track_ids_for_date: Callable[[str], set[str]],
        spacing_seconds: int,
        log_mode: str,
        enabled: bool,
        no_post_mode: bool,
        max_posts: int = 5,
        min_days: int | None = None,
        min_daily_streams: int | None = None,
        min_pct_change: float | None = None,
        min_score: float | None = None,
        priority_ready: Callable[[], bool] | None = None,
        priority_track_ids: list[str] | None = None,
        on_post: Callable[[], None] | None = None,
    ) -> None:
        self.script_dir = script_dir
        self.stats_date = stats_date
        self.on_post = on_post
        self.priority_track_ids = tuple(dict.fromkeys(priority_track_ids or []))
        self.track_ids = tuple(dict.fromkeys([*self.priority_track_ids, *track_ids]))
        self._priority_track_id_set = set(self.priority_track_ids)
        self.export_web_data = export_web_data
        self.load_history_track_ids_for_date = load_history_track_ids_for_date
        self.spacing_seconds = spacing_seconds
        self.log_mode = log_mode
        self.enabled = enabled
        self.no_post_mode = no_post_mode
        self.max_posts = max_posts
        self.min_days = min_days
        self.min_daily_streams = min_daily_streams
        self.min_pct_change = min_pct_change
        self.min_score = min_score
        self.priority_ready = priority_ready or (lambda: True)
        self._checked: set[str] = set()
        self._posted: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._post_state = {"posted_count": 0, "last_post_at": 0.0}

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="ready-best-day-since-posts", daemon=True)
        self._thread.start()

    def stop(self) -> set[str]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            return set(self._posted)

    def post_state(self) -> dict[str, float]:
        with self._lock:
            return dict(self._post_state)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._post_newly_ready_track():
                continue
            if self._done():
                return
            self._stop.wait(3.0)

    def _done(self) -> bool:
        with self._lock:
            # Keep scanning every watched track even after the normal early-post
            # cap: a biggest-day-of-the-year record is posted unconditionally and
            # the per-track subprocess enforces the cap on the rest itself.
            return len(self._checked) >= len(self.track_ids)

    def _post_newly_ready_track(self) -> bool:
        done_ids = self.load_history_track_ids_for_date(self.stats_date)
        for track_id in self.track_ids:
            with self._lock:
                if track_id in self._checked:
                    continue
            if track_id not in done_ids:
                continue
            is_priority_track = track_id in self._priority_track_id_set
            if not is_priority_track and not self.priority_ready():
                return False
            with self._lock:
                self._checked.add(track_id)

            print(f"Best-day-since check ready during streams run: {track_id}")
            self.export_web_data(stats_date=self.stats_date)

            best_day_script = self.script_dir / "tools" / "scripts" / "post_best_day_since_twitter.py"
            cmd = [sys.executable, str(best_day_script), self.stats_date, "--only-track", track_id]
            if self.min_days is not None:
                cmd.extend(["--min-days", str(self.min_days)])
            if self.min_daily_streams is not None:
                cmd.extend(["--min-daily-streams", str(self.min_daily_streams)])
            if self.min_pct_change is not None:
                cmd.extend(["--min-pct-change", str(self.min_pct_change)])
            if self.min_score is not None:
                cmd.extend(["--early-min-score", str(self.min_score)])
            if self.no_post_mode:
                cmd.append("--no-post")

            _wait_before_post(
                label=f"early best-day-since ({track_id})",
                should_post=not self.no_post_mode,
                state=self._post_state,
                spacing_seconds=self.spacing_seconds,
                log_mode=self.log_mode,
            )
            result = _run_subprocess(cmd, check=False, env={"TWITTER_POST_PRIORITY": "0"})
            # exit 0 = posted, exit 2 = posted a biggest-day-of-the-year card
            # that bypassed every cap.
            if result.returncode in (0, 2):
                kind = "biggest day of the year" if result.returncode == 2 else "record"
                print(f"Best-day-since posted early during streams run ({kind}): {track_id}")
                _mark_post_done(should_post=not self.no_post_mode, state=self._post_state)
                with self._lock:
                    self._posted.add(track_id)
                _run_on_post_callback(self.on_post)
                return True
            if result.returncode != 3:
                print(f"Early best-day-since check failed for {track_id} (exit {result.returncode}); skipping.")
            return True
        return False


def _run_streams_post(
    cmd: list[str],
    *,
    label: str,
    should_post: bool,
    state: dict[str, float],
    spacing_seconds: int,
    log_mode: str,
    post_priority: str | None = None,
) -> None:
    env_extra = {"TWITTER_POST_PRIORITY": str(post_priority)} if post_priority is not None else None
    last_returncode = 0
    for attempt in range(1, FINALIZE_POST_RETRY_ATTEMPTS + 1):
        _wait_before_post(
            label=label,
            should_post=should_post,
            state=state,
            spacing_seconds=spacing_seconds,
            log_mode=log_mode,
        )

        if attempt > 1:
            print(f"Retrying {label} ({attempt}/{FINALIZE_POST_RETRY_ATTEMPTS})...")

        result = _run_subprocess(cmd, check=False, env=env_extra)
        last_returncode = result.returncode
        if result.returncode == 0:
            _mark_post_done(should_post=should_post, state=state)
            return

        print(f"{label} failed (exit {result.returncode}) on attempt {attempt}/{FINALIZE_POST_RETRY_ATTEMPTS}.")
        if attempt < FINALIZE_POST_RETRY_ATTEMPTS and FINALIZE_POST_RETRY_SLEEP_SECONDS > 0:
            print(f"Waiting {FINALIZE_POST_RETRY_SLEEP_SECONDS}s before retrying {label}...")
            time.sleep(FINALIZE_POST_RETRY_SLEEP_SECONDS)

    raise SystemExit(f"{label} failed after {FINALIZE_POST_RETRY_ATTEMPTS} attempt(s) (last exit {last_returncode}).")


def _wait_before_post(
    *,
    label: str,
    should_post: bool,
    state: dict[str, float],
    spacing_seconds: int,
    log_mode: str,
) -> None:
    if should_post and state["posted_count"] > 0:
        elapsed_since_post = time.perf_counter() - state.get("last_post_at", 0.0)
        wait_s = max(0.0, spacing_seconds - elapsed_since_post)
        if wait_s > 0:
            print(f"Waiting {int(wait_s)}s before next Twitter post ({label})...")
            time.sleep(wait_s)
        elif log_mode == "verbose":
            print(f"Twitter spacing already satisfied before {label}.")


def _mark_post_done(*, should_post: bool, state: dict[str, float]) -> None:
    if should_post:
        state["posted_count"] += 1
        state["last_post_at"] = time.perf_counter()


def _run_on_post_callback(callback: Callable[[], None] | None) -> None:
    """Fire a watcher's optional post-success hook (e.g. push best_day_since.json
    to R2 early). Must never break the watcher loop."""
    if callback is None:
        return
    try:
        callback()
    except Exception as exc:  # a hook failure must not stall posting
        print(f"[finalize] on-post hook failed (non-blocking): {exc}")


def _run(
    ctx: FinalizeContext,
    cmd: list[str],
    *,
    label: str,
    should_post: bool,
    state: dict[str, float],
    post_priority: str | None = None,
) -> None:
    _run_streams_post(
        cmd,
        label=label,
        should_post=should_post,
        state=state,
        spacing_seconds=ctx.post_spacing_seconds,
        log_mode=ctx.log_mode,
        post_priority=post_priority,
    )


def _export_web_data_once(ctx: FinalizeContext, *, force: bool = False) -> None:
    run_dir = update_streams_dir(ctx.stats_date)
    export_lock = run_dir / "exported.lock"
    daily_site_history = run_dir / "site_history.json"
    r2_export_lock = run_dir / "r2_exported.lock"
    if export_lock.exists() and not force and not ctx.test_mode:
        if daily_site_history.exists():
            print(f"Web export already done for {ctx.stats_date} (exported.lock exists), skipping.")
            return
        print(
            f"Web export lock exists for {ctx.stats_date}, but {daily_site_history.name} "
            "is missing; rebuilding export."
        )

    print("Re-exporting web data...")
    allow_r2 = not ctx.local_test_mode and not ctx.test_mode
    if allow_r2 and r2_export_lock.exists():
        # An earlier partial export (mid-run, before the day was fully complete)
        # already uploaded once and left this lock behind. This call only runs
        # once the day's collection is 100% done, so it must always push the
        # final, complete numbers to R2 rather than staying skipped forever.
        print(f"R2 export lock exists for {ctx.stats_date} from an earlier partial export; forcing a final R2 upload with complete data.")
        r2_export_lock.unlink()
    ctx.export_web_data(allow_r2=allow_r2, stats_date=ctx.stats_date)
    if not ctx.local_test_mode and not ctx.test_mode:
        run_dir.mkdir(parents=True, exist_ok=True)
        export_lock.touch()
    print("Web export done.")


def _ensure_daily_site_history(ctx: FinalizeContext) -> None:
    daily_site_history = update_streams_dir(ctx.stats_date) / "site_history.json"
    if daily_site_history.exists():
        return
    print(f"Daily site history missing for {ctx.stats_date}; exporting web data before post.")
    ctx.export_web_data(allow_r2=False, stats_date=ctx.stats_date)


def _refresh_release_dates(ctx: FinalizeContext) -> bool:
    if ctx.local_test_mode:
        print("[LOCAL-TEST] Skip Spotify release-date refresh.")
        return False

    release_dates_script = ctx.script_dir / "tools" / "scripts" / "update_release_dates.py"
    print("Refreshing Spotify API release dates...")
    result = _run_subprocess(
        [sys.executable, str(release_dates_script)],
        cwd=str(ctx.repo_root),
        check=False,
    )
    if result.returncode != 0:
        print(f"Release-date refresh exited with code {result.returncode}; continuing.")
        return False
    return True


def _post_streams_image(ctx: FinalizeContext, state: dict[str, float]) -> None:
    if ctx.debug_daily_mode:
        print("[DEBUG-DAILY] Skip: Twitter, forecast, images, git, notify.")
        return

    post_script = ctx.script_dir / "tools" / "scripts" / "post_streams_twitter.py"
    if ctx.no_post_mode:
        print("Skipping Twitter post (--no-post).")
        _run(
            ctx,
            [sys.executable, str(post_script), ctx.summary["stats_date"], "--no-post"],
            label="top 20 songs image (no-post)",
            should_post=False,
            state=state,
        )
        return

    if not _streams_post_ready(ctx):
        print("Skipping Twitter post: blocking tracks are still pending.")
        return

    print("Posting top 20 songs image to Twitter...")
    _run(
        ctx,
        [sys.executable, str(post_script), ctx.summary["stats_date"]],
        label="top 20 songs image",
        should_post=True,
        state=state,
    )
    print("Twitter post done.")


def _post_daily_recap_card(ctx: FinalizeContext, state: dict[str, float]) -> None:
    if ctx.debug_daily_mode:
        print("[DEBUG-DAILY] Skip: daily recap card.")
        return

    post_script = ctx.script_dir / "tools" / "scripts" / "post_weekend_streams_twitter.py"
    cmd = [sys.executable, str(post_script), ctx.summary["stats_date"]]
    if not _is_weekend_stats_date(ctx.summary["stats_date"]):
        cmd.append("--force-weekday")

    if ctx.no_post_mode:
        print("Generating daily recap card only (--no-post).")
        cmd.append("--no-post")
        _run(
            ctx,
            cmd,
            label="daily recap card (no-post)",
            should_post=False,
            state=state,
        )
        return

    if not _streams_post_ready(ctx):
        print("Skipping daily recap card: blocking tracks are still pending.")
        return

    _ensure_daily_site_history(ctx)

    print("Posting daily recap card to Twitter...")
    _run(
        ctx,
        cmd,
        label="daily recap card",
        should_post=True,
        state=state,
    )
    print("Daily recap card done.")


def _post_weekend_song_gainers(ctx: FinalizeContext, state: dict[str, float]) -> None:
    if ctx.debug_daily_mode:
        print("[DEBUG-DAILY] Skip: weekend song gainers.")
        return
    if not _is_weekend_stats_date(ctx.summary["stats_date"]):
        print("Weekend song gainers skipped: stats date is not Saturday or Sunday.")
        return
    if not _streams_post_ready(ctx):
        print("Weekend song gainers skipped: blocking tracks are still pending.")
        return

    post_script = ctx.script_dir / "tools" / "scripts" / "post_weekend_song_gainers.py"
    cmd = [
        sys.executable,
        str(post_script),
        ctx.summary["stats_date"],
        "--min-pct",
        "5",
        "--post-spacing-seconds",
        str(ctx.post_spacing_seconds),
    ]
    if ctx.no_post_mode:
        cmd.append("--no-post")

    print("Posting weekend +5% song gainers...")
    _run(
        ctx,
        cmd,
        label="weekend song gainers",
        should_post=not ctx.no_post_mode,
        state=state,
    )
    print("Weekend song gainers done.")


def _post_song_overtakes(ctx: FinalizeContext, state: dict[str, float]) -> None:
    if ctx.debug_daily_mode:
        print("[DEBUG-DAILY] Skip: song overtakes.")
        return
    if not _streams_post_ready(ctx):
        print("Song overtakes skipped: blocking tracks are still pending.")
        return

    post_script = ctx.script_dir / "tools" / "scripts" / "post_song_overtakes.py"
    cmd = [
        sys.executable,
        str(post_script),
        ctx.summary["stats_date"],
        "--post-spacing-seconds",
        str(ctx.post_spacing_seconds),
    ]
    if ctx.no_post_mode:
        cmd.append("--no-post")

    print("Posting non-extra song overtakes...")
    _run(
        ctx,
        cmd,
        label="song overtakes",
        should_post=not ctx.no_post_mode,
        state=state,
    )
    print("Song overtakes done.")


def _post_stream_milestones(ctx: FinalizeContext, state: dict[str, float]) -> None:
    if ctx.debug_daily_mode:
        print("[DEBUG-DAILY] Skip: stream milestones.")
        return
    if not _streams_post_ready(ctx):
        print("Stream milestones skipped: blocking tracks are still pending.")
        return

    post_script = ctx.script_dir / "tools" / "scripts" / "post_stream_milestones.py"
    cmd = [
        sys.executable,
        str(post_script),
        ctx.summary["stats_date"],
        "--post-spacing-seconds",
        str(ctx.post_spacing_seconds),
    ]
    if ctx.no_post_mode:
        cmd.append("--no-post")

    print("Posting song stream milestones...")
    _run(
        ctx,
        cmd,
        label="stream milestones",
        should_post=not ctx.no_post_mode,
        state=state,
    )
    print("Stream milestones done.")


def _streams_post_ready(ctx: FinalizeContext) -> bool:
    """Only allow stream posts after the full target collection is complete."""
    if ctx.summary.get("all_done"):
        return True

    pending = [
        row for row in ctx.summary.get("results", [])
        if row and row.get("status") == "pending"
    ]
    if not pending:
        print("Streams post blocked: collection summary is not complete.")
        return False

    print(
        "Streams post blocked by incomplete collection: "
        + ", ".join(str(row.get("title") or row.get("track_id")) for row in pending[:5])
    )
    return False


def _update_artist_metadata(ctx: FinalizeContext) -> bool:
    if ctx.artist_thread is None:
        return False

    print("Updating artist metadata...")
    ctx.artist_thread.join(timeout=60)
    if ctx.local_test_mode:
        print("[LOCAL-TEST] Skip writing artist metadata.")
        return False
    else:
        ctx.update_artist_metadata(pre_scraped=ctx.artist_result[0])
        return True


def _run_forecast_and_image_refresh(ctx: FinalizeContext) -> None:
    print("Rebuilding expected milestones forecast...")
    _run_subprocess(
        [sys.executable, str(ctx.script_dir / "tools" / "scripts" / "forecast_milestones.py")],
        check=True,
    )
    print("Expected milestones forecast done.")

    print("Updating track image URLs from Spotify (cache-aware)...")
    _run_subprocess(
        [sys.executable, str(ctx.script_dir / "extras" / "update_all_track_images.py")],
        check=False,
    )
    print("Track image scrape done.")

    print("Refreshing image URLs + track_covers.json...")
    _run_subprocess(
        [sys.executable, str(ctx.repo_root / "scripts" / "fill_images.py")],
        check=True,
    )
    print("Image URLs and track_covers.json done.")


def _post_one_album(
    ctx: FinalizeContext,
    state: dict[str, float],
    album_img_script: Path,
    album: str,
    *,
    post_priority: str | None = None,
) -> None:
    """Generate + post a single album update card, honoring locks and completeness.

    Shared by the targeted posts (ALBUM_UPDATE_TARGETS/gainers) and the
    daily all-albums sweep so both paths skip an album already posted (by the
    other path or an earlier retry) instead of double-posting.
    """
    if album in ctx.posted_album_updates:
        print(f"Album update already posted during streams run: {album}")
        return
    block_reason = generate_album_update_image.holiday_collection_post_block_reason(
        album,
        ctx.summary["stats_date"],
    )
    if block_reason:
        print(f"Album update skipped ({album}): {block_reason}")
        return
    if not ctx.album_tracks_done_for(album, ctx.summary["stats_date"]):
        try:
            sections = ctx.load_album_sections_flat()
            album_ids = {
                ctx.extract_track_id(t.get("url") or t.get("spotify_url") or "")
                for sec in sections if sec.get("album") == album
                for t in sec.get("tracks", [])
            } - {""}
            done = ctx.load_history_track_ids_for_date(ctx.summary["stats_date"])
            print(f"Album update skipped ({album}): {len(album_ids - done)}/{len(album_ids)} tracks manquants.")
        except Exception:
            print(f"Album update skipped ({album}): impossible de verifier les tracks.")
        return

    if (
        not ctx.no_post_mode
        and generate_album_update_image.album_update_already_posted(album, ctx.summary["stats_date"])
    ):
        lock_name = generate_album_update_image.album_update_lock_path(
            album,
            ctx.summary["stats_date"],
        ).name
        print(f"Album update already posted ({lock_name}): {album}")
        return

    print(f"Generating album update image: {album} ...")
    album_cmd = [sys.executable, str(album_img_script), album, ctx.summary["stats_date"]]
    if not ctx.no_post_mode:
        album_cmd.append("--post")
    try:
        _run(
            ctx,
            album_cmd,
            label=f"album update ({album})",
            should_post=not ctx.no_post_mode,
            state=state,
            post_priority=post_priority,
        )
    except SystemExit as exc:
        print(f"Album update skipped after failure ({album}): {exc}")


def _post_albums_daily(ctx: FinalizeContext, state: dict[str, float]) -> None:
    if not ctx.no_post_mode and not ctx.summary.get("all_done"):
        print("Skipping top eras post: not all tracks are done yet.")
        return

    albums_post_script = ctx.script_dir / "tools" / "scripts" / "post_albums_twitter.py"
    albums_cmd = [sys.executable, str(albums_post_script), ctx.summary["stats_date"]]
    if ctx.no_post_mode:
        albums_cmd.append("--no-post")
    _run(
        ctx,
        albums_cmd,
        label="top eras image",
        should_post=not ctx.no_post_mode,
        state=state,
    )


def _is_misc_album(name: str) -> bool:
    norm = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    parts = set(norm.split("_"))
    if "misc" in parts or "standalone" in parts:
        return True
    return norm in {"miscellaneous", "standalone_extras", "standalone_and_extras"}


def _all_album_names(ctx: FinalizeContext) -> list[str]:
    seen: list[str] = []
    for section in ctx.load_album_sections_flat():
        album = (section.get("album") or "").strip()
        if not album or album in seen or _is_misc_album(album):
            continue
        seen.append(album)
    return seen


def _album_daily_total(album: str, stats_date: str) -> int:
    sections, _canonical_name = generate_album_update_image.load_album_sections(album, stats_date)
    if not sections:
        return 0
    hist = generate_album_update_image.load_history_for_album(sections, stats_date)
    return sum(
        int(hist.get(track["track_id"], {}).get("daily") or 0)
        for section in sections
        for track in section.get("tracks", [])
    )


def _rank_albums_for_posting(albums: list[str], stats_date: str) -> list[str]:
    """Order albums best-first for posting via ``score_album_update`` (same
    scoring shape as the best-day-since song scorer: daily abs/%% gain, weekly
    %% gain, rarity, grower, plus album record + majority-positive bonuses).
    Falls back to a plain daily-total sort if scoring fails — ordering must
    never block a post."""
    try:
        scored = score_album_update.score_albums(list(albums), date_cls.fromisoformat(stats_date))
    except Exception as exc:
        print(f"[all-albums] score ranking failed ({exc}); sorting by daily total.")
        return sorted(albums, key=lambda album: _album_daily_total(album, stats_date), reverse=True)

    ranked = [item["album"] for item in scored]
    for album in albums:
        if album not in ranked:
            ranked.append(album)
    top = ", ".join(
        f"{item['album']} {float(item.get('score') or 0.0):.0f}"
        for item in scored[:5]
    )
    if top:
        print(f"[all-albums] score order (top 5): {top}")
    return ranked


def _album_post_queue(ctx: FinalizeContext, stats_date: str) -> list[str]:
    """Daily album post order (decision 2026-09-03): score every non-Misc album
    with ``score_album_update``, post the two best, then Showgirl / TTPD if they
    are not already in that top 2, then the rest strictly by score. No targeted
    gain/gainer/majority scans anymore — the score already carries the
    biggest-day / best-ever / majority-positive bonuses.

    Decision 2026-09-04: the `BOTTOM_ALBUMS_SKIPPED` lowest-scoring albums in
    that "rest by score" tail don't get a card at all that day — the two
    guaranteed top scorers and the forced primary targets (Showgirl/TTPD) are
    never at risk of being dropped, only the genuine bottom of the ranking."""
    albums = _all_album_names(ctx)
    blocked: list[str] = []
    postable: list[str] = []
    for album in albums:
        block_reason = generate_album_update_image.holiday_collection_post_block_reason(album, stats_date)
        if block_reason:
            blocked.append(album)
        else:
            postable.append(album)
    if blocked:
        print(f"[all-albums] skipped before ranking (not postable): {', '.join(blocked)}")

    ranked = _rank_albums_for_posting(postable, stats_date)
    if len(ranked) <= 2:
        return ranked
    primary_cf = {name.casefold() for name in PRIMARY_ALBUM_UPDATE_TARGETS}
    top2 = ranked[:2]
    done_cf = {album.casefold() for album in top2}
    forced = [album for album in ranked if album.casefold() in primary_cf and album.casefold() not in done_cf]
    done_cf |= {album.casefold() for album in forced}
    tail = [album for album in ranked if album.casefold() not in done_cf]

    dropped: list[str] = []
    if len(tail) > BOTTOM_ALBUMS_SKIPPED:
        dropped = tail[-BOTTOM_ALBUMS_SKIPPED:]
        tail = tail[: -BOTTOM_ALBUMS_SKIPPED]

    queue = top2 + forced + tail
    if forced:
        print(f"[all-albums] forced after top 2: {', '.join(forced)}")
    if dropped:
        print(f"[all-albums] skipped (bottom {len(dropped)} by score): {', '.join(dropped)}")
    return queue


def _post_all_albums(ctx: FinalizeContext, state: dict[str, float]) -> None:
    """Every non-Misc album, posted independently (not as a thread), in
    ``_album_post_queue`` order (top 2 by score -> Showgirl/TTPD -> rest by
    score). No album cards on weekend stats dates. In the normal daily run the
    finalize loop interleaves these with the other post steps; this function is
    the ``--post-only all-albums`` entrypoint (and the weekday fallback)."""
    stats_date = ctx.summary["stats_date"]
    if _is_weekend_stats_date(stats_date):
        print("All-albums posts skipped: no album cards on weekend stats dates.")
        return

    if not ctx.no_post_mode and not ctx.summary.get("all_done"):
        print("Skipping all-albums posts: not all tracks are done yet.")
        return

    queue = _album_post_queue(ctx, stats_date)
    if not queue:
        print("[all-albums] No albums found.")
        return

    album_img_script = ctx.script_dir / "tools" / "scripts" / "generate_album_update_image.py"
    for album in queue:
        _post_one_album(ctx, state, album_img_script, album, post_priority="4")


def _post_debut_releases(ctx: FinalizeContext, state: dict[str, float]) -> None:
    label = "debut release posts"
    _wait_before_post(
        label="debut release posts",
        should_post=not ctx.no_post_mode,
        state=state,
        spacing_seconds=ctx.post_spacing_seconds,
        log_mode=ctx.log_mode,
    )
    result = run_debut_release_posts(ctx.summary["stats_date"], no_post=ctx.no_post_mode, priority=0)
    if result != 0:
        raise SystemExit(f"{label} failed (exit {result}).")
    _mark_post_done(should_post=not ctx.no_post_mode, state=state)


def _post_best_day_since(ctx: FinalizeContext, state: dict[str, float]) -> None:
    if not ctx.summary.get("all_done"):
        print("Best-day-since posts skipped: not all tracks are done yet.")
        return

    best_day_script = ctx.script_dir / "tools" / "scripts" / "post_best_day_since_twitter.py"
    cmd = [
        sys.executable,
        str(best_day_script),
        ctx.summary["stats_date"],
        "--post-spacing-seconds",
        str(ctx.post_spacing_seconds),
    ]
    if ctx.posted_best_day_since_tracks:
        cmd.extend(["--exclude-tracks", ",".join(sorted(ctx.posted_best_day_since_tracks))])
    if ctx.no_post_mode:
        cmd.append("--no-post")

    print("Posting best-day-since stream cards...")
    _run(
        ctx,
        cmd,
        label="best-day-since posts",
        should_post=not ctx.no_post_mode,
        state=state,
    )


def _post_best_day_since_recap_fallback(ctx: FinalizeContext, state: dict[str, float]) -> None:
    """Safety net for the best-day-since recap + era recap cards (decision
    2026-09-04): they normally post as replies in the Top Songs thread
    (post_streams_twitter.py -> post_best_day_since_twitter.build_recap_thread_posts).
    ``--limit 0`` skips individual song posts and runs only the (unchanged)
    era-recap-batch + global-recap code, which checks the same lock files the
    thread path writes -- so this is a no-op print when the thread already
    covered it, and a real standalone fallback if that thread post failed.
    Must run before best-day-since candidate listing so era-recap suppression
    is correctly in place either way."""
    if not ctx.summary.get("all_done"):
        return
    best_day_script = ctx.script_dir / "tools" / "scripts" / "post_best_day_since_twitter.py"
    cmd = [sys.executable, str(best_day_script), ctx.summary["stats_date"], "--limit", "0"]
    if ctx.no_post_mode:
        cmd.append("--no-post")
    _run(
        ctx,
        cmd,
        label="best-day-since recap (fallback)",
        should_post=not ctx.no_post_mode,
        state=state,
    )


def _best_day_since_candidate_tracks(ctx: FinalizeContext) -> list[str]:
    """Ask post_best_day_since_twitter.py which individual song track ids the
    finalize batch would post today (same selection/caps as the old
    single-call batch), without posting anything. Used to interleave each
    song 1-for-1 with the album queue instead of posting the whole batch as
    one alternation turn. Never blocks finalize -- returns [] on any failure."""
    best_day_script = ctx.script_dir / "tools" / "scripts" / "post_best_day_since_twitter.py"
    cmd = [sys.executable, str(best_day_script), ctx.summary["stats_date"], "--list-batch-candidates"]
    if ctx.posted_best_day_since_tracks:
        cmd.extend(["--exclude-tracks", ",".join(sorted(ctx.posted_best_day_since_tracks))])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except Exception as exc:
        print(f"[best-day-since] Failed to list batch candidates: {exc}")
        return []
    for line in reversed(result.stdout.splitlines()):
        if line.startswith("BATCH_CANDIDATES_JSON:"):
            try:
                data = json.loads(line.split(":", 1)[1].strip())
                return [str(tid) for tid in (data.get("track_ids") or [])]
            except Exception as exc:
                print(f"[best-day-since] Failed to parse batch candidates ({exc}): {line}")
                return []
    print("[best-day-since] No BATCH_CANDIDATES_JSON line found; skipping individual song posts.")
    if result.stdout:
        print(result.stdout[-2000:])
    if result.stderr:
        print(result.stderr[-2000:])
    return []


def _post_one_best_day_track(
    ctx: FinalizeContext,
    state: dict[str, float],
    best_day_script: Path,
    track_id: str,
) -> None:
    """Post a single best-day-since song card already selected by
    _best_day_since_candidate_tracks, so it can take its own turn in the
    album/other-posts alternation instead of the whole batch posting at once."""
    if track_id in ctx.posted_best_day_since_tracks:
        return
    cmd = [
        sys.executable,
        str(best_day_script),
        ctx.summary["stats_date"],
        "--post-batch-track",
        track_id,
    ]
    if ctx.no_post_mode:
        cmd.append("--no-post")
    _run(
        ctx,
        cmd,
        label=f"best-day-since post ({track_id})",
        should_post=not ctx.no_post_mode,
        state=state,
    )
    ctx.posted_best_day_since_tracks.add(track_id)


def _post_spotlight_gainers(ctx: FinalizeContext, state: dict[str, float]) -> None:
    if not ctx.all_album_tracks_done(ctx.summary["stats_date"]):
        print("Stream highlights skipped: not all album tracks are done yet.")
        return

    highlights_script = ctx.script_dir / "tools" / "scripts" / "post_stream_highlights_thread.py"
    print("Posting separate stream highlight tables (daily %, weekly %)...")
    cmd = [
        sys.executable,
        str(highlights_script),
        ctx.summary["stats_date"],
        "--limit",
        "10",
        "--post-spacing-seconds",
        str(ctx.post_spacing_seconds),
    ]
    if ctx.no_post_mode:
        cmd.append("--no-post")
    _run(
        ctx,
        cmd,
        label="stream highlights tables",
        should_post=not ctx.no_post_mode,
        state=state,
    )


def _post_throwback_thread(ctx: FinalizeContext, state: dict[str, float]) -> None:
    if not ctx.throwback_action or not ctx.throwback_event:
        print("Throwback skipped: missing --throwback-action/--throwback-event.")
        return

    throwback_script = ctx.script_dir / "tools" / "scripts" / "post_throwback_thread.py"
    cmd = [
        sys.executable,
        str(throwback_script),
        ctx.summary["stats_date"],
        "--action",
        ctx.throwback_action,
        "--event",
        ctx.throwback_event,
    ]
    if ctx.throwback_label:
        cmd.extend(["--label", ctx.throwback_label])
    if ctx.throwback_force:
        cmd.append("--force")
    if ctx.no_post_mode:
        cmd.append("--no-post")

    print("Posting throwback stream thread...")
    _run(
        ctx,
        cmd,
        label="throwback stream thread",
        should_post=not ctx.no_post_mode,
        state=state,
    )


def _start_spotlight_gainers(ctx: FinalizeContext) -> threading.Thread:
    errors: list[BaseException] = []

    def _target() -> None:
        try:
            _post_spotlight_gainers(ctx, {"posted_count": 0, "last_post_at": 0.0})
        except BaseException as exc:
            errors.append(exc)
            print(f"Stream highlights thread failed: {exc}")

    thread = threading.Thread(
        target=_target,
        name="spotlight-gainers-posts",
        daemon=False,
    )
    thread.post_errors = errors  # type: ignore[attr-defined]
    thread.start()
    return thread


def _start_background_task(label: str, target: Callable[[], None]) -> threading.Thread:
    errors: list[BaseException] = []

    def _target() -> None:
        try:
            target()
        except BaseException as exc:
            errors.append(exc)
            print(f"{label} failed: {exc}")

    thread = threading.Thread(
        target=_target,
        name=label.lower().replace(" ", "-"),
        daemon=False,
    )
    thread.task_errors = errors  # type: ignore[attr-defined]
    thread.start()
    return thread


def _join_background_task(
    thread: threading.Thread | None,
    label: str,
    timer: StepTimer | None = None,
) -> None:
    if thread is None:
        return
    start = time.perf_counter()
    thread.join()
    if timer is not None:
        timer.add(f"wait {label}", time.perf_counter() - start)
    errors = getattr(thread, "task_errors", [])
    if errors:
        raise SystemExit(f"{label} failed: {errors[0]}")


def _is_weekend_stats_date(stats_date: str) -> bool:
    return date_cls.fromisoformat(stats_date).weekday() in (5, 6)


def _run_swift_top_charts_if_needed(ctx: FinalizeContext) -> None:
    try:
        if not ctx.summary.get("all_done"):
            print("Skipping Swift Top charts: not all tracks are done yet.")
            return

        stats_date = date_cls.fromisoformat(ctx.summary["stats_date"])
        if stats_date.weekday() != 3:
            return

        gate_status = check_swift_top_gate(stats_date, source="streams")
        if gate_status == "done":
            print(f"Swift Top charts already generated for {ctx.summary['stats_date']}.")
            return
        if gate_status == "waiting":
            print(
                "Swift Top charts waiting for Spotify charts "
                f"for {ctx.summary['stats_date']}."
            )
            return

        print(f"\nStreams and Spotify charts ready - generating Swift Top 100 for {ctx.summary['stats_date']} ...")
        swift_top_100_script = ctx.repo_root / "collectors" / "billboard" / "swift_top_100.py"
        top_100_result = _run_subprocess(
            [sys.executable, str(swift_top_100_script), "--date", ctx.summary["stats_date"], "--variant", "all"],
            cwd=str(ctx.repo_root),
            check=False,
        )
        if top_100_result.returncode != 0:
            print(f"Swift Top 100 exited with code {top_100_result.returncode}.")
            return

        print("Swift Top 100 generated successfully.")
        mark_swift_top_done(stats_date, source="streams")
        git_commit_and_push(ctx.repo_root, f"charts swift top 100 and albums {ctx.summary['stats_date']}")
    except Exception as exc:
        print(f"Swift Top charts trigger failed - {exc}")


def _regenerate_home_highlights_cache(ctx: FinalizeContext) -> None:
    """Best-effort refresh of the Charts Gallery highlights/version R2 cache.

    Recomputes cache/home_highlights.json and cache/version.json (read by
    tsm-frontend/api) from the freshly exported local data. Must never affect
    the streams pipeline: on failure, the frontend's own cache-on-miss
    fallback (api/data/precompute_cache.py) recomputes it live instead.
    """
    if ctx.local_test_mode or ctx.test_mode:
        return
    try:
        script = ctx.repo_root / "scripts" / "generate_home_highlights.py"
        result = _run_subprocess(
            [sys.executable, str(script), "--quiet"],
            cwd=str(ctx.repo_root),
            check=False,
        )
        if result.returncode != 0:
            print(f"Home highlights cache refresh exited with code {result.returncode}.")
    except Exception as exc:
        print(f"Home highlights cache refresh failed - {exc}")


# Étapes de post invocables individuellement via `update_streams.py --post-only`.
POST_ONLY_STEPS = {
    "top-eras": _post_albums_daily,
    "all-albums": _post_all_albums,
    "top20": _post_streams_image,
    "top45": _post_streams_image,
    "recap": _post_daily_recap_card,
    "weekend-gainers": _post_weekend_song_gainers,
    "milestones": _post_stream_milestones,
    "overtakes": _post_song_overtakes,
    "best-day-since": _post_best_day_since,
    "debut": _post_debut_releases,
    "gainers": _post_spotlight_gainers,
}


def run_post_only_steps(ctx: FinalizeContext, step_names: list[str]) -> None:
    """Rejoue uniquement les étapes de post demandées sur l'history existante.

    Mêmes règles que la finalisation quotidienne : garde-fous de complétude,
    locks anti-double-post et restrictions de jour (cards album hors week-end,
    top eras hors week-end) s'appliquent toujours."""
    post_state = dict(ctx.initial_post_state or {"posted_count": 0, "last_post_at": 0.0})
    failures: list[str] = []
    for name in step_names:
        step = POST_ONLY_STEPS[name]
        print(f"[POST-ONLY] Running step: {name}")
        try:
            step(ctx, post_state)
        except SystemExit as exc:
            print(f"[POST-ONLY] {name} failed: {exc}")
            failures.append(f"{name} ({exc})")
    if failures:
        raise SystemExit("post-only step(s) failed: " + " | ".join(failures))


def run_final_update_tasks(ctx: FinalizeContext) -> None:
    timer = StepTimer("finalize")
    post_state = dict(ctx.initial_post_state or {"posted_count": 0, "last_post_at": 0.0})
    if ctx.throwback_mode:
        with timer.step("throwback thread"):
            _post_throwback_thread(ctx, post_state)
        timer.summary()
        return
    if not ctx.summary.get("all_done") and not ctx.debug_daily_mode and not ctx.local_test_mode:
        print("Finalization stopped: streams collection is not complete.")
        timer.summary()
        return

    try:
        with timer.step("artist metadata"):
            artist_metadata_updated = _update_artist_metadata(ctx)
        print("Skipping Spotify API release-date refresh during finalization.")
        with timer.step("web export"):
            _export_web_data_once(ctx, force=artist_metadata_updated)

        with timer.step("home highlights cache"):
            _regenerate_home_highlights_cache(ctx)

        forecast_thread = None
        if not ctx.debug_daily_mode and not ctx.local_test_mode:
            print("Starting forecast/image refresh in background...")
            forecast_thread = _start_background_task(
                "forecast/image refresh",
                lambda: _run_forecast_and_image_refresh(ctx),
            )

        # Chaque étape de post est indépendante : un échec (après ses propres
        # retries) ne doit pas annuler les posts suivants ni le commit git.
        # Les échecs sont collectés et re-signalés en fin de finalisation.
        post_step_failures: list[str] = []

        def _guarded_post_step(step_label: str, fn) -> None:
            with timer.step(step_label):
                try:
                    fn()
                except SystemExit as exc:
                    print(f"{step_label} failed; continuing finalization: {exc}")
                    post_step_failures.append(f"{step_label} ({exc})")

        # Ordre de post (décision 2026-09-03) :
        #   1. récap weekend  2. top eras  3. top songs
        #   4. cards album (2 meilleurs au score_album_update -> Showgirl/TTPD
        #      s'ils n'y sont pas -> reste par score) ALTERNÉES 1-pour-1 avec les
        #      autres posts (debut, best-day-since, weekend gainers, overtakes,
        #      milestones)
        #   5. tables gainers (spotlight), toujours en dernier
        _guarded_post_step("weekend recap card", lambda: _post_daily_recap_card(ctx, post_state))

        if not ctx.debug_daily_mode and not ctx.local_test_mode:
            _guarded_post_step("top eras post", lambda: _post_albums_daily(ctx, post_state))

        _guarded_post_step("top 20 songs post", lambda: _post_streams_image(ctx, post_state))
        if not ctx.debug_daily_mode and not ctx.local_test_mode:
            _guarded_post_step(
                "best-day-since recap (fallback)",
                lambda: _post_best_day_since_recap_fallback(ctx, post_state),
            )

        album_queue: list[str] = []
        if not ctx.debug_daily_mode and not ctx.local_test_mode:
            if _is_weekend_stats_date(ctx.summary["stats_date"]):
                print("Album update posts skipped: no album cards on weekend stats dates.")
            else:
                album_queue = _album_post_queue(ctx, ctx.summary["stats_date"])
                # A same-album overtake posts that album's update as the overtake
                # image (flat total ranking) from the "song overtakes" step, so
                # drop it from the normal daily queue to avoid a double post.
                try:
                    import post_song_overtakes
                    overtake_albums = {
                        a.casefold()
                        for a in post_song_overtakes.same_album_overtake_albums(ctx.summary["stats_date"])
                    }
                except Exception as exc:
                    print(f"[all-albums] same-album overtake check failed ({exc}); keeping full queue.")
                    overtake_albums = set()
                if overtake_albums:
                    kept = [a for a in album_queue if a.casefold() not in overtake_albums]
                    removed = [a for a in album_queue if a.casefold() in overtake_albums]
                    if removed:
                        print(f"[all-albums] handled as same-album overtake image: {', '.join(removed)}")
                    album_queue = kept
        album_img_script = ctx.script_dir / "tools" / "scripts" / "generate_album_update_image.py"

        other_steps: list[tuple[str, Callable[[], None]]] = []
        if not ctx.debug_daily_mode and not ctx.local_test_mode:
            other_steps.append(("debut posts", lambda: _post_debut_releases(ctx, post_state)))
            # Each candidate gets its own alternation turn against the album
            # queue (decision 2026-09-04), instead of the whole best-day-since
            # batch posting back to back as a single turn.
            if ctx.summary.get("all_done"):
                best_day_script = ctx.script_dir / "tools" / "scripts" / "post_best_day_since_twitter.py"
                for track_id in _best_day_since_candidate_tracks(ctx):
                    other_steps.append((
                        f"best-day-since post ({track_id})",
                        lambda tid=track_id: _post_one_best_day_track(ctx, post_state, best_day_script, tid),
                    ))
            else:
                print("Best-day-since posts skipped: not all tracks are done yet.")
        other_steps.append(("weekend song gainers", lambda: _post_weekend_song_gainers(ctx, post_state)))
        other_steps.append(("song overtakes", lambda: _post_song_overtakes(ctx, post_state)))
        other_steps.append(("stream milestones", lambda: _post_stream_milestones(ctx, post_state)))

        album_iter = iter(album_queue)
        other_iter = iter(other_steps)
        album_done = other_done = False
        while not (album_done and other_done):
            if not album_done:
                album = next(album_iter, None)
                if album is None:
                    album_done = True
                else:
                    _guarded_post_step(
                        f"album update ({album})",
                        lambda album=album: _post_one_album(
                            ctx, post_state, album_img_script, album, post_priority="4"
                        ),
                    )
            if not other_done:
                step = next(other_iter, None)
                if step is None:
                    other_done = True
                else:
                    _guarded_post_step(step[0], step[1])

        if ctx.debug_daily_mode or ctx.local_test_mode:
            return

        # Always last: biggest daily/weekly gainers should not delay the core
        # daily posts.
        _guarded_post_step("stream highlights tables", lambda: _post_spotlight_gainers(ctx, post_state))

        _join_background_task(forecast_thread, "forecast/image refresh", timer)

        if ctx.test_mode:
            print("[TEST] Skipping cleanup, git commit/push, and Swift Top chart triggers.")
        else:
            with timer.step("cleanup artifacts"):
                cleanup_generated_artifacts()
            print("Git commit and push...")
            with timer.step("git commit/push"):
                git_commit_and_push(ctx.repo_root, f"daily final export {ctx.summary['stats_date']}")
            with timer.step("swift top charts"):
                _run_swift_top_charts_if_needed(ctx)

        if post_step_failures:
            raise SystemExit(
                "Finalization completed with failed post step(s): "
                + " | ".join(post_step_failures)
            )
    finally:
        timer.summary()
