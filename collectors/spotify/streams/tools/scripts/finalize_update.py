from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Callable

from core.data_paths import update_streams_dir
from core.retention import cleanup_generated_artifacts
from git_ops import git_commit_and_push


ALBUM_UPDATE_TARGETS = (
    "The Life of a Showgirl",
    "THE TORTURED POETS DEPARTMENT",
)


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
    ) -> None:
        self.script_dir = script_dir
        self.stats_date = stats_date
        self.export_web_data = export_web_data
        self.album_tracks_done_for = album_tracks_done_for
        self.spacing_seconds = spacing_seconds
        self.log_mode = log_mode
        self.enabled = enabled
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
            return set(ALBUM_UPDATE_TARGETS).issubset(self._posted)

    def _post_newly_ready_albums(self) -> bool:
        for album in ALBUM_UPDATE_TARGETS:
            with self._lock:
                if album in self._posted:
                    continue
            if not self.album_tracks_done_for(album, self.stats_date):
                continue

            print(f"Album update ready during streams run: {album}")
            print("Exporting current web data before early album post...")
            self.export_web_data(stats_date=self.stats_date)

            album_img_script = self.script_dir / "tools" / "scripts" / "generate_album_update_image.py"
            _run_streams_post(
                [sys.executable, str(album_img_script), album, self.stats_date, "--post"],
                label=f"early album update ({album})",
                should_post=True,
                state=self._post_state,
                spacing_seconds=self.spacing_seconds,
                log_mode=self.log_mode,
            )
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
    if should_post and state["posted_count"] > 0:
        elapsed_since_post = time.perf_counter() - state.get("last_post_at", 0.0)
        wait_s = max(0.0, spacing_seconds - elapsed_since_post)
        if wait_s > 0:
            print(f"Waiting {int(wait_s)}s before next Twitter post ({label})...")
            time.sleep(wait_s)
        elif log_mode == "verbose":
            print(f"Twitter spacing already satisfied before {label}.")

    subprocess.run(cmd, check=False)

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
    export_lock = update_streams_dir(ctx.stats_date) / "exported.lock"
    if export_lock.exists() and not force:
        print(f"Web export already done for {ctx.stats_date} (exported.lock exists), skipping.")
        return

    print("Re-exporting web data...")
    ctx.export_web_data(allow_r2=not ctx.local_test_mode, stats_date=ctx.stats_date)
    if not ctx.local_test_mode:
        export_lock.parent.mkdir(parents=True, exist_ok=True)
        export_lock.touch()
    print("Web export done.")


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

        if not ctx.summary.get("all_done"):
            print("Skipping weekend streams post: not all tracks are done yet.")
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

    if not ctx.summary.get("all_done"):
        print("Skipping Twitter post: not all tracks are done yet.")
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
    subprocess.run(
        [sys.executable, str(ctx.script_dir / "tools" / "scripts" / "forecast_milestones.py")],
        check=True,
    )
    print("Expected milestones forecast done.")

    print("Updating track image URLs from Spotify (cache-aware)...")
    subprocess.run(
        [sys.executable, str(ctx.script_dir / "extras" / "update_all_track_images.py")],
        check=False,
    )
    print("Track image scrape done.")

    print("Refreshing image URLs + track_covers.json...")
    subprocess.run(
        [sys.executable, str(ctx.repo_root / "scripts" / "fill_images.py")],
        check=True,
    )
    print("Image URLs and track_covers.json done.")


def _post_album_updates(ctx: FinalizeContext, state: dict[str, float]) -> None:
    if _is_weekend_stats_date(ctx.summary["stats_date"]):
        print("Weekend detected: skipping separate album update posts.")
        return

    album_img_script = ctx.script_dir / "tools" / "scripts" / "generate_album_update_image.py"

    for album in ALBUM_UPDATE_TARGETS:
        if album in ctx.posted_album_updates:
            print(f"Album update already posted during streams run: {album}")
            continue
        if ctx.album_tracks_done_for(album, ctx.summary["stats_date"]):
            print(f"Generating album update image: {album} ...")
            album_cmd = [sys.executable, str(album_img_script), album, ctx.summary["stats_date"]]
            if not ctx.no_post_mode:
                album_cmd.append("--post")
            _run(
                ctx,
                album_cmd,
                label=f"album update ({album})",
                should_post=not ctx.no_post_mode,
                state=state,
            )
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


def _post_spotlight_gainers(ctx: FinalizeContext, state: dict[str, float]) -> None:
    if not ctx.all_album_tracks_done(ctx.summary["stats_date"]):
        print("Stream highlights skipped: not all album tracks are done yet.")
        return

    highlights_script = ctx.script_dir / "tools" / "scripts" / "post_stream_highlights_thread.py"
    print("Posting unique stream highlights (daily %, weekly %, best-day-since)...")
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
    print("Best-day-since posts included in stream highlights thread; skipping separate posts.")
    return

    if _is_weekend_stats_date(ctx.summary["stats_date"]):
        print("Weekend detected: skipping separate best-day-since posts.")
        return

    if not ctx.all_album_tracks_done(ctx.summary["stats_date"]):
        print("Best-day-since posts skipped: not all album tracks are done yet.")
        return

    print("Posting top best-day-since songs to @tsmuseum13...")
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
    thread = threading.Thread(
        target=_post_spotlight_gainers,
        args=(ctx, {"posted_count": 0, "last_post_at": 0.0}),
        name="spotlight-gainers-posts",
        daemon=False,
    )
    thread.start()
    return thread


def _is_weekend_stats_date(stats_date: str) -> bool:
    return date_cls.fromisoformat(stats_date).weekday() in (5, 6)


def _run_swift_top_charts_if_needed(ctx: FinalizeContext) -> None:
    try:
        stats_date = date_cls.fromisoformat(ctx.summary["stats_date"])
        if stats_date.weekday() != 2:
            return

        print(f"\nWednesday detected - generating Swift Top 100 for {ctx.summary['stats_date']} ...")
        swift_top_100_script = ctx.repo_root / "collectors" / "billboard" / "swift_top_100.py"
        top_100_result = subprocess.run(
            [sys.executable, str(swift_top_100_script), "--date", ctx.summary["stats_date"], "--variant", "all"],
            cwd=str(ctx.repo_root),
            check=False,
        )
        if top_100_result.returncode != 0:
            print(f"Swift Top 100 exited with code {top_100_result.returncode}.")
            return

        print("Swift Top 100 generated successfully.")
        print(f"Generating Swift Top Albums for {ctx.summary['stats_date']} ...")
        swift_top_albums_script = ctx.repo_root / "collectors" / "billboard" / "swift_top_albums.py"
        albums_result = subprocess.run(
            [sys.executable, str(swift_top_albums_script), "--date", ctx.summary["stats_date"], "--variant", "all", "--upload"],
            cwd=str(ctx.repo_root),
            check=False,
        )
        if albums_result.returncode == 0:
            print("Swift Top Albums generated successfully.")
            git_commit_and_push(ctx.repo_root, f"charts swift top 100 and albums {ctx.summary['stats_date']}")
        else:
            print(f"Swift Top Albums exited with code {albums_result.returncode}.")
    except Exception as exc:
        print(f"Swift Top charts trigger failed - {exc}")


def run_final_update_tasks(ctx: FinalizeContext) -> None:
    post_state = dict(ctx.initial_post_state or {"posted_count": 0, "last_post_at": 0.0})
    if ctx.throwback_mode:
        _post_throwback_thread(ctx, post_state)
        return

    artist_metadata_updated = _update_artist_metadata(ctx)
    _export_web_data_once(ctx, force=artist_metadata_updated)

    spotlight_thread = None
    if (
        not ctx.debug_daily_mode
        and not ctx.local_test_mode
        and not _is_weekend_stats_date(ctx.summary["stats_date"])
    ):
        spotlight_thread = _start_spotlight_gainers(ctx)

    _post_streams_image(ctx, post_state)

    if ctx.debug_daily_mode or ctx.local_test_mode:
        return

    _post_album_updates(ctx, post_state)
    _post_albums_daily(ctx, post_state)
    _run_forecast_and_image_refresh(ctx)
    if spotlight_thread is not None:
        spotlight_thread.join()
    _post_best_day_since(ctx, post_state)

    cleanup_generated_artifacts()
    print("Git commit and push...")
    git_commit_and_push(ctx.repo_root, f"daily final export {ctx.summary['stats_date']}")
    _run_swift_top_charts_if_needed(ctx)
