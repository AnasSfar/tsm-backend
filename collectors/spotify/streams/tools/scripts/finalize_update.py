from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Callable

from core.data_paths import update_streams_dir
from core.swift_top_gate import check_swift_top_gate, mark_swift_top_done
from core.retention import cleanup_generated_artifacts
from git_ops import git_commit_and_push
import generate_albums_image
import generate_album_update_image
import post_gainer_thread
from post_debut_releases import post_debut_releases as run_debut_release_posts
from release_targets import recent_release_album_names


ALBUM_UPDATE_TARGETS = (
    "The Life of a Showgirl",
    "THE TORTURED POETS DEPARTMENT",
)
ALBUM_UPDATE_GAIN_THRESHOLD_PCT = 15.0
GAINER_ALBUM_UPDATE_MIN_TRACKS = 2
GAINER_ALBUM_UPDATE_LIMIT = 5
GAINER_ALBUM_UPDATE_MIN_BASELINE = 1000
FINALIZE_POST_RETRY_ATTEMPTS = max(1, int(os.getenv("FINALIZE_POST_RETRY_ATTEMPTS", "3")))
FINALIZE_POST_RETRY_SLEEP_SECONDS = max(0, int(os.getenv("FINALIZE_POST_RETRY_SLEEP_SECONDS", "60")))


def _subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
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


class ReadyAlbumUpdatePoster:
    """Post ready album updates early after exporting the partial site state."""

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
        target_albums: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.script_dir = script_dir
        self.stats_date = stats_date
        self.export_web_data = export_web_data
        self.album_tracks_done_for = album_tracks_done_for
        self.spacing_seconds = spacing_seconds
        self.log_mode = log_mode
        self.enabled = enabled
        self.target_albums = tuple(dict.fromkeys(target_albums or ALBUM_UPDATE_TARGETS))
        self._posted: set[str] = set()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._post_state = {"posted_count": 0, "last_post_at": 0.0}

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="ready-album-posts", daemon=True)
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
            if self._post_newly_ready_albums():
                continue
            if self._all_targets_posted():
                return
            self._stop.wait(1.0)

    def _all_targets_posted(self) -> bool:
        with self._lock:
            return set(self.target_albums).issubset(self._posted)

    def _post_newly_ready_albums(self) -> bool:
        for album in self.target_albums:
            with self._lock:
                if album in self._posted:
                    continue
            if not self.album_tracks_done_for(album, self.stats_date):
                continue

            print(f"Album update ready during streams run: {album}")
            print("Exporting current web data before early album post...")
            self.export_web_data(stats_date=self.stats_date)

            album_img_script = self.script_dir / "tools" / "scripts" / "generate_album_update_image.py"
            try:
                _run_streams_post(
                    [sys.executable, str(album_img_script), album, self.stats_date, "--post"],
                    label=f"early album update ({album})",
                    should_post=True,
                    state=self._post_state,
                    spacing_seconds=self.spacing_seconds,
                    log_mode=self.log_mode,
                )
            except SystemExit as exc:
                print(f"Early album update skipped after failure ({album}): {exc}")
                with self._lock:
                    self._posted.add(album)
                return True
            with self._lock:
                self._posted.add(album)
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
) -> None:
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

        result = _run_subprocess(cmd, check=False)
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


def _run(ctx: FinalizeContext, cmd: list[str], *, label: str, should_post: bool, state: dict[str, float]) -> None:
    _run_streams_post(
        cmd,
        label=label,
        should_post=should_post,
        state=state,
        spacing_seconds=ctx.post_spacing_seconds,
        log_mode=ctx.log_mode,
    )


def _export_web_data_once(ctx: FinalizeContext, *, force: bool = False) -> None:
    run_dir = update_streams_dir(ctx.stats_date)
    export_lock = run_dir / "exported.lock"
    r2_export_lock = run_dir / "r2_exported.lock"
    if export_lock.exists() and not force and not ctx.test_mode:
        print(f"Web export already done for {ctx.stats_date} (exported.lock exists), skipping.")
        return

    print("Re-exporting web data...")
    allow_r2 = not ctx.local_test_mode and not ctx.test_mode and not r2_export_lock.exists()
    if r2_export_lock.exists():
        print(f"R2 export already done for {ctx.stats_date} (r2_exported.lock exists), skipping R2 upload.")
    ctx.export_web_data(allow_r2=allow_r2, stats_date=ctx.stats_date)
    if not ctx.local_test_mode and not ctx.test_mode:
        run_dir.mkdir(parents=True, exist_ok=True)
        export_lock.touch()
    print("Web export done.")


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

    if _is_weekend_stats_date(ctx.summary["stats_date"]):
        post_script = ctx.script_dir / "tools" / "scripts" / "post_weekend_streams_twitter.py"
        if ctx.no_post_mode:
            print("Weekend detected: generating combined streams image only (--no-post).")
            _run(
                ctx,
                [sys.executable, str(post_script), ctx.summary["stats_date"], "--no-post"],
                label="weekend streams image (no-post)",
                should_post=False,
                state=state,
            )
            return

        if not _streams_post_ready(ctx):
            print("Skipping weekend streams post: blocking tracks are still pending.")
            return

        print("Weekend detected: posting one combined streams image to Twitter...")
        _run(
            ctx,
            [sys.executable, str(post_script), ctx.summary["stats_date"]],
            label="weekend streams image",
            should_post=True,
            state=state,
        )
        print("Weekend streams post done.")
        return

    post_script = ctx.script_dir / "tools" / "scripts" / "post_streams_twitter.py"
    if ctx.no_post_mode:
        print("Skipping Twitter post (--no-post).")
        _run(
            ctx,
            [sys.executable, str(post_script), ctx.summary["stats_date"], "--no-post"],
            label="streams image (no-post)",
            should_post=False,
            state=state,
        )
        return

    if not _streams_post_ready(ctx):
        print("Skipping Twitter post: blocking tracks are still pending.")
        return

    print("Posting streams image to Twitter...")
    _run(
        ctx,
        [sys.executable, str(post_script), ctx.summary["stats_date"]],
        label="streams image",
        should_post=True,
        state=state,
    )
    print("Twitter post done.")


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


def _album_gain_update_targets(stats_date: str, *, threshold_pct: float = ALBUM_UPDATE_GAIN_THRESHOLD_PCT) -> list[dict]:
    try:
        covers = generate_albums_image.load_covers()
        track_map = generate_albums_image.load_album_track_map()
        today, yest, week = generate_albums_image.load_history(stats_date)
        rows = generate_albums_image.build_album_rows(
            today,
            yest,
            week,
            track_map,
            covers,
            merge_eras=False,
        )
    except Exception as exc:
        print(f"Album gain scan skipped: {exc}")
        return []

    targets: list[dict] = []
    for row in rows:
        daily = int(row.get("daily_streams") or 0)
        yest_daily = int(row.get("yest_daily") or 0)
        if daily <= 0 or yest_daily <= 0:
            continue
        pct = (daily - yest_daily) / yest_daily * 100
        if pct >= threshold_pct:
            targets.append({
                "album": row.get("album") or "",
                "daily_streams": daily,
                "yest_daily": yest_daily,
                "gain_pct": pct,
            })

    targets = [target for target in targets if target["album"]]
    targets.sort(key=lambda target: (target["gain_pct"], target["daily_streams"]), reverse=True)
    return targets


def _album_by_track_id(ctx: FinalizeContext) -> dict[str, str]:
    result: dict[str, str] = {}
    for section in ctx.load_album_sections_flat():
        album = section.get("album") or ""
        if not album:
            continue
        for track in section.get("tracks", []):
            track_id = ctx.extract_track_id(track.get("url") or track.get("spotify_url") or "")
            if track_id and track_id not in result:
                result[track_id] = album
    return result


def _album_gainer_update_targets(
    ctx: FinalizeContext,
    stats_date: str,
    *,
    limit: int = GAINER_ALBUM_UPDATE_LIMIT,
    min_baseline: int = GAINER_ALBUM_UPDATE_MIN_BASELINE,
    min_tracks: int = GAINER_ALBUM_UPDATE_MIN_TRACKS,
) -> list[dict]:
    try:
        daily = post_gainer_thread._pick_gainers(
            stats_date,
            compare_days=1,
            limit=limit,
            min_baseline=min_baseline,
        )
        weekly = post_gainer_thread._pick_gainers(
            stats_date,
            compare_days=7,
            limit=limit,
            min_baseline=min_baseline,
        )
    except Exception as exc:
        print(f"Album gainer scan skipped: {exc}")
        return []

    album_by_track_id = _album_by_track_id(ctx)
    targets_by_album: dict[str, dict] = {}
    for period, rows in (("daily", daily), ("weekly", weekly)):
        counts: dict[str, int] = {}
        best_pct: dict[str, float] = {}
        for row in rows:
            album = album_by_track_id.get(row.get("track_id") or "") or (row.get("track") or {}).get("album") or ""
            if not album:
                continue
            counts[album] = counts.get(album, 0) + 1
            best_pct[album] = max(best_pct.get(album, 0.0), float(row.get("pct") or 0.0))

        for album, count in counts.items():
            if count < min_tracks:
                continue
            target = targets_by_album.setdefault(
                album,
                {
                    "album": album,
                    "daily_count": 0,
                    "weekly_count": 0,
                    "best_pct": 0.0,
                    "periods": [],
                },
            )
            target[f"{period}_count"] = count
            target["best_pct"] = max(float(target["best_pct"]), best_pct.get(album, 0.0))
            target["periods"].append(period)

    targets = list(targets_by_album.values())
    targets.sort(
        key=lambda target: (
            max(int(target["daily_count"]), int(target["weekly_count"])),
            float(target["best_pct"]),
        ),
        reverse=True,
    )
    return targets


def _album_update_targets(ctx: FinalizeContext) -> list[str]:
    albums: list[str] = list(ALBUM_UPDATE_TARGETS)
    recent_albums = recent_release_album_names(
        ctx.load_album_sections_flat(),
        ctx.summary["stats_date"],
    )
    if recent_albums:
        print("Recent release album targets: " + ", ".join(recent_albums))
    for album in recent_albums:
        if album not in albums:
            albums.append(album)
    return albums


def _post_album_updates(ctx: FinalizeContext, state: dict[str, float]) -> None:
    album_img_script = ctx.script_dir / "tools" / "scripts" / "generate_album_update_image.py"
    is_weekend = _is_weekend_stats_date(ctx.summary["stats_date"])
    gain_targets = _album_gain_update_targets(
        ctx.summary["stats_date"],
        threshold_pct=0.0 if is_weekend else ALBUM_UPDATE_GAIN_THRESHOLD_PCT,
    )
    if is_weekend:
        gain_targets = [target for target in gain_targets if float(target["gain_pct"]) > 0.0]
    if gain_targets:
        print(
            ("Weekend positive album update scan: " if is_weekend else "Album update gain scan: ")
            + ", ".join(
                f"{target['album']} +{target['gain_pct']:.1f}%"
                for target in gain_targets
            )
        )
    elif is_weekend:
        print("Weekend detected: no positive album updates found.")

    gainer_targets = [] if is_weekend else _album_gainer_update_targets(ctx, ctx.summary["stats_date"])
    if gainer_targets:
        print(
            "Album update gainer scan: "
            + ", ".join(
                f"{target['album']} "
                f"daily={target['daily_count']} weekly={target['weekly_count']}"
                for target in gainer_targets
            )
        )

    albums_to_post: list[str] = [] if is_weekend else _album_update_targets(ctx)
    for target in gain_targets:
        album = target["album"]
        if album not in albums_to_post:
            albums_to_post.append(album)
    for target in gainer_targets:
        album = target["album"]
        if album not in albums_to_post:
            albums_to_post.append(album)

    for album in albums_to_post:
        if album in ctx.posted_album_updates:
            print(f"Album update already posted during streams run: {album}")
            continue
        if ctx.album_tracks_done_for(album, ctx.summary["stats_date"]):
            if (
                not ctx.no_post_mode
                and generate_album_update_image.album_update_already_posted(album, ctx.summary["stats_date"])
            ):
                lock_name = generate_album_update_image.album_update_lock_path(
                    album,
                    ctx.summary["stats_date"],
                ).name
                print(f"Album update already posted ({lock_name}): {album}")
                continue
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
                )
            except SystemExit as exc:
                print(f"Album update skipped after failure ({album}): {exc}")
            continue

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


def _post_albums_daily(ctx: FinalizeContext, state: dict[str, float]) -> None:
    if _is_weekend_stats_date(ctx.summary["stats_date"]):
        print("Weekend detected: skipping separate albums daily post (included in combined streams image).")
        return

    if not ctx.no_post_mode and not ctx.summary.get("all_done"):
        print("Skipping albums daily post: not all tracks are done yet.")
        return

    albums_post_script = ctx.script_dir / "tools" / "scripts" / "post_albums_twitter.py"
    albums_cmd = [sys.executable, str(albums_post_script), ctx.summary["stats_date"]]
    if ctx.no_post_mode:
        albums_cmd.append("--no-post")
    _run(
        ctx,
        albums_cmd,
        label="albums daily image",
        should_post=not ctx.no_post_mode,
        state=state,
    )


def _post_debut_releases(ctx: FinalizeContext, state: dict[str, float]) -> None:
    label = "debut release posts"
    _wait_before_post(
        label="debut release posts",
        should_post=not ctx.no_post_mode,
        state=state,
        spacing_seconds=ctx.post_spacing_seconds,
        log_mode=ctx.log_mode,
    )
    result = run_debut_release_posts(ctx.summary["stats_date"], no_post=ctx.no_post_mode)
    if result != 0:
        raise SystemExit(f"{label} failed (exit {result}).")
    _mark_post_done(should_post=not ctx.no_post_mode, state=state)


def _post_spotlight_gainers(ctx: FinalizeContext, state: dict[str, float]) -> None:
    if not ctx.all_album_tracks_done(ctx.summary["stats_date"]):
        print("Stream highlights skipped: not all album tracks are done yet.")
        return

    highlights_script = ctx.script_dir / "tools" / "scripts" / "post_stream_highlights_thread.py"
    print("Posting separate stream highlight threads (daily %, weekly %, best-day-since)...")
    cmd = [
        sys.executable,
        str(highlights_script),
        ctx.summary["stats_date"],
        "--limit",
        "5",
    ]
    if ctx.no_post_mode:
        cmd.append("--no-post")
    _run(
        ctx,
        cmd,
        label="stream highlights thread",
        should_post=not ctx.no_post_mode,
        state=state,
    )


def _post_best_day_since(ctx: FinalizeContext, state: dict[str, float]) -> None:
    if not ctx.all_album_tracks_done(ctx.summary["stats_date"]):
        print("Best-day-since posts skipped: not all album tracks are done yet.")
        return

    print("Posting top long-range best-day-since songs to @tsmuseum13...")
    post_script = ctx.script_dir / "tools" / "scripts" / "post_best_day_since_twitter.py"
    cmd = [sys.executable, str(post_script), ctx.summary["stats_date"], "--limit", "3"]
    if ctx.no_post_mode:
        cmd.append("--no-post")
    _run(
        ctx,
        cmd,
        label="best-day-since songs",
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

        forecast_thread = None
        if not ctx.debug_daily_mode and not ctx.local_test_mode:
            print("Starting forecast/image refresh in background...")
            forecast_thread = _start_background_task(
                "forecast/image refresh",
                lambda: _run_forecast_and_image_refresh(ctx),
            )

        spotlight_thread = None
        if (
            not ctx.debug_daily_mode
            and not ctx.local_test_mode
        ):
            spotlight_thread = _start_spotlight_gainers(ctx)

        with timer.step("streams post"):
            _post_streams_image(ctx, post_state)

        if ctx.debug_daily_mode or ctx.local_test_mode:
            return

        with timer.step("debut posts"):
            _post_debut_releases(ctx, post_state)
        with timer.step("album update posts"):
            _post_album_updates(ctx, post_state)
        with timer.step("albums daily post"):
            _post_albums_daily(ctx, post_state)
        if spotlight_thread is not None:
            start = time.perf_counter()
            spotlight_thread.join()
            timer.add("wait stream highlights", time.perf_counter() - start)
            spotlight_errors = getattr(spotlight_thread, "post_errors", [])
            if spotlight_errors:
                raise SystemExit(f"stream highlights thread failed: {spotlight_errors[0]}")
        with timer.step("best-day-since posts"):
            _post_best_day_since(ctx, post_state)
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
    finally:
        timer.summary()
