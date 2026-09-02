#!/usr/bin/env python3
"""
update_youtube.py — YouTube views collector for Swifties Charts.

Collecte les vues quotidiennes de toutes les vidéos de la chaîne officielle
Taylor Swift via YouTube Data API v3.

Usage:
    python -m collectors.youtube.update_youtube
    python -m collectors.youtube.update_youtube --dry-run
    python -m collectors.youtube.update_youtube --debug
    python -m collectors.youtube.update_youtube --no-post     # aucun post X + pas de ntfy
    python -m collectors.youtube.update_youtube --no-notify   # pas de ntfy, mais cards first-day OK (run_youtube.bat)
    python -m collectors.youtube.update_youtube --date 2026-04-25   # date d'activité voulue (pas la date du run)
    python -m collectors.youtube.update_youtube --bootstrap  # découverte complète initiale
    python -m collectors.youtube.update_youtube --preview    # aperçu card "first 24h views"
    python -m collectors.youtube.update_youtube --post-first-day VIDEO_ID  # interne, voir ci-dessous

Quand une vidéo tout juste découverte est écrite pour la première fois dans
le CSV, une tâche Planificateur de tâches Windows one-off est créée pour
published_at+24h (voir _schedule_first_day_task) : à cette heure précise,
elle relance ce script avec --post-first-day VIDEO_ID, qui fetch le live
view count, poste la card "views in its first 24 hours" sur @swiftiescharts,
puis se désinscrit elle-même. La collecte quotidienne normale
(post_first_day_views) reste un filet de sécurité au cas où cette tâche
n'aurait pas pu être créée ou ne se serait pas déclenchée. --no-post
désactive la planification ET la notification ntfy ; --no-notify ne coupe
que la ntfy (les cards first-day restent planifiées/postées — c'est ce que
run_youtube.bat utilise). --bootstrap ne planifie jamais (découverte en
masse, aucune vidéo n'est "tout juste" publiée). --preview génère un aperçu de la card à tout moment (fetch live,
sans écrire le CSV ni poster) pour la vidéo actuellement en attente de son
1er daily_views.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .core.api import chunked, fetch_video_stats
from .core.channel import (
    discover_new_videos,
    discover_new_videos_short_circuit,
    load_video_db,
    save_video_db,
    update_video_db,
)
from .core.config import (
    BATCH_SIZE,
    CSV_FIELDNAMES,
    CSV_PATH,
    DISCOGRAPHY_SONGS_PATH,
    HISTORY_PATH,
    NTFY_TOPIC,
    REPO_ROOT,
    TITLE_CSV_FIELDNAMES,
    TITLE_HISTORY_PATH,
    TOOLS_JSON_DIR,
    VIDEO_DB_PATH,
    VIDEO_GROUPS_PATH,
    YOUTUBE_API_KEY,
)
from .core.csv_utils import (
    append_rows,
    date_already_collected,
    get_last_views,
    has_collection_before,
    read_csv_rows,
    remove_rows_for_date,
    save_last_views,
)
from .core.git_ops import git_commit_and_push
from .core.title_groups import build_title_rows, write_title_history


def _youtube_collection_date() -> str:
    tz_name = os.getenv("YOUTUBE_COLLECTION_TZ", "America/New_York")
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        print(f"[WARN] Timezone YouTube inconnue: {tz_name!r}; fallback UTC.")
        tz = timezone.utc
    return datetime.now(tz).date().isoformat()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Collecte les vues YouTube quotidiennes pour Taylor Swift."
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch les données, affiche le résultat, n'écrit rien et ne commit pas.",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Écrit CSV + JSON state mais skip git et notifications.",
    )
    p.add_argument(
        "--no-post",
        action="store_true",
        help=(
            "Pas de post X/Twitter : ni la card 'first 24h views' (planification "
            "+ filet de sécurité), ni la notification ntfy quotidienne."
        ),
    )
    p.add_argument(
        "--no-notify",
        action="store_true",
        help=(
            "Coupe uniquement la notification ntfy quotidienne. Les cards "
            "'first 24h views' restent planifiées et postées. C'est ce que "
            "run_youtube.bat utilise."
        ),
    )
    p.add_argument(
        "--date",
        default=None,
        help=(
            "Force la date d'activité des lignes écrites (YYYY-MM-DD) — la "
            "journée que les vues représentent, pas la date du run. Sans "
            "l'option: date du run − 1 jour (le run à minuit NY mesure la "
            "journée qui vient de se terminer)."
        ),
    )
    p.add_argument(
        "--bootstrap",
        action="store_true",
        help="Découverte complète de toute la chaîne (à lancer une seule fois).",
    )
    p.add_argument(
        "--commit",
        action="store_true",
        help="Git commit + push après la collecte (désactivé par défaut).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Remplace les lignes CSV existantes pour la date collectée.",
    )
    p.add_argument(
        "--preview",
        action="store_true",
        help=(
            "Génère un aperçu de la card 'first 24h views' pour la vidéo actuellement en "
            "attente de son 1er daily_views (fetch live, sans écrire le CSV ni poster)."
        ),
    )
    p.add_argument(
        "--post-first-day",
        metavar="VIDEO_ID",
        default=None,
        help=(
            "Poste la card 'first 24h views' pour cette vidéo (fetch live, poste, écrit le "
            "lock, désinscrit sa propre tâche planifiée). Appelé par la tâche Windows "
            "one-off créée à la découverte de la vidéo (published_at + 24h) — pas destiné à "
            "un usage manuel courant."
        ),
    )
    return p.parse_args()


def _fmt_views(n: int | str) -> str:
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


def _int_or_none(value: object) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pct_change(current: int | None, previous: int | None) -> str:
    if current is None or previous is None or previous <= 0:
        return ""
    return f"{((current - previous) / previous) * 100:.6f}"


def _latest_rows_before(rows: list[dict], target_date: str) -> list[dict]:
    dates = sorted({row.get("date", "") for row in rows if row.get("date", "") < target_date})
    if not dates:
        return []
    latest = dates[-1]
    return [row for row in rows if row.get("date") == latest]


def _latest_date_before(rows: list[dict], target_date: str) -> str:
    dates = sorted({row.get("date", "") for row in rows if row.get("date", "") < target_date})
    return dates[-1] if dates else ""


def _days_between(previous_date: str, target_date: str) -> int | None:
    if not previous_date:
        return None
    try:
        prev = datetime.strptime(previous_date, "%Y-%m-%d").date()
        current = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return None
    days = (current - prev).days
    return days if days > 0 else None


def _last_total_views_from_csv(csv_path: Path, target_date: str) -> dict[str, int]:
    rows = read_csv_rows(csv_path)
    latest_rows = _latest_rows_before(rows, target_date)
    out: dict[str, int] = {}
    for row in latest_rows:
        video_id = row.get("video_id")
        total = _int_or_none(row.get("total_views"))
        if video_id and total is not None:
            out[video_id] = total
    return out


def _rank_rows(rows: list[dict], field: str, rank_field: str) -> None:
    for row in rows:
        row[rank_field] = ""
    eligible = [row for row in rows if _int_or_none(row.get(field)) is not None]
    ranked = sorted(
        eligible,
        key=lambda row: (-(_int_or_none(row.get(field)) or 0), str(row.get("title") or "")),
    )
    for index, row in enumerate(ranked, 1):
        row[rank_field] = index


def enrich_chart_rows(
    rows: list[dict],
    *,
    existing_rows: list[dict],
    target_date: str,
    key_field: str,
) -> list[dict]:
    previous_rows = _latest_rows_before(existing_rows, target_date)
    previous_by_key = {
        str(row.get(key_field) or ""): row
        for row in previous_rows
        if row.get(key_field)
    }

    _rank_rows(rows, "daily_views", "rank")
    _rank_rows(rows, "total_views", "total_rank")
    previous_ranked = [dict(row) for row in previous_rows]
    _rank_rows(previous_ranked, "daily_views", "rank")
    _rank_rows(previous_ranked, "total_views", "total_rank")
    previous_ranked_by_key = {
        str(row.get(key_field) or ""): row
        for row in previous_ranked
        if row.get(key_field)
    }

    for row in rows:
        key = str(row.get(key_field) or "")
        previous = previous_by_key.get(key, {})
        previous_ranked_row = previous_ranked_by_key.get(key, {})
        daily = _int_or_none(row.get("daily_views"))
        previous_daily = _int_or_none(previous.get("daily_views"))
        previous_rank = _int_or_none(previous_ranked_row.get("rank"))
        previous_total_rank = _int_or_none(previous_ranked_row.get("total_rank"))
        rank = _int_or_none(row.get("rank"))
        total_rank = _int_or_none(row.get("total_rank"))

        row["previous_rank"] = previous_rank or ""
        row["rank_change"] = (previous_rank - rank) if previous_rank and rank else ""
        row["previous_total_rank"] = previous_total_rank or ""
        row["total_rank_change"] = (previous_total_rank - total_rank) if previous_total_rank and total_rank else ""
        row["daily_change"] = (daily - previous_daily) if daily is not None and previous_daily is not None else ""
        row["daily_change_pct"] = _pct_change(daily, previous_daily)

    return sorted(rows, key=lambda row: _int_or_none(row.get("rank")) or 999999)


def maybe_upload_youtube_to_r2(today: str) -> None:
    require_upload = os.getenv("REQUIRE_R2_UPLOAD", "").strip().lower() in ("1", "true", "yes")
    if os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("0", "false", "no"):
        print("[INFO] R2 upload skippé (UPLOAD_TO_R2 explicitement désactivé).")
        if require_upload:
            raise RuntimeError("R2 upload required but UPLOAD_TO_R2 is disabled")
        return
    try:
        from scripts import r2
        ok = r2.upload_youtube()
        if ok:
            print("[INFO] R2 upload YouTube terminé.")
        else:
            print("[WARN] R2 upload YouTube skippé.")
    except Exception as exc:
        print(f"[WARN] R2 upload YouTube échoué (non bloquant): {exc}")


def _notify(title: str, message: str) -> None:
    try:
        import sys
        sys.path.insert(0, str(REPO_ROOT / "collectors" / "spotify"))
        from core.notify import send
        send(NTFY_TOPIC, message, title=title, tags="youtube,musical_note")
    except Exception as e:
        print(f"[NOTIFY] Échec: {e}", flush=True)


FIRST_DAY_POSTED_DIR = TOOLS_JSON_DIR / "first_day_posted"


FIRST_DAY_VIEWS_MAX_PUBLISH_LAG_DAYS = 4


def _is_recent_publish(published_at: str, today: str, max_lag_days: int = FIRST_DAY_VIEWS_MAX_PUBLISH_LAG_DAYS) -> bool:
    """True if published_at is within max_lag_days of today (both UTC dates).
    Used to tell a genuinely new release (video didn't exist before today,
    so its whole total_views belongs to today) apart from an old video that
    just became publicly listed/discovered (its total_views already includes
    years of prior views — attributing all of it to today would be fake
    data, same reasoning as FIRST_DAY_VIEWS_MAX_PUBLISH_LAG_DAYS for the
    first-24h tweet)."""
    published_at = (published_at or "").strip()
    try:
        published = datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ").date()
    except ValueError:
        return False
    return (date.fromisoformat(today) - published).days <= max_lag_days


def _first_daily_video_ids(rows_with_daily: list[dict], existing_video_rows: list[dict], today: str) -> set[str]:
    """Video ids whose daily_views today is their FIRST-EVER real daily delta.

    A brand new video is first collected with daily_views blank (no previous
    snapshot to diff against, per the collector's core exact-delta rule). The
    very next daily run produces its first real delta, which is what "views
    in its first 24h" means here — so a video qualifies when it has exactly
    one earlier appearance in the CSV, and that appearance is before today.

    Also requires published_at to be recent (within
    FIRST_DAY_VIEWS_MAX_PUBLISH_LAG_DAYS of today): discover_new_videos can
    surface a video that only just became public/listed but was actually
    uploaded long ago, and that one already has a real view count baked in —
    not "views in its first 24h"."""
    prior_dates: dict[str, set[str]] = {}
    for row in existing_video_rows:
        vid = row.get("video_id")
        day = row.get("date")
        if vid and day and day < today:
            prior_dates.setdefault(vid, set()).add(day)

    target = date.fromisoformat(today)
    ids: set[str] = set()
    for row in rows_with_daily:
        vid = row["video_id"]
        if len(prior_dates.get(vid, set())) != 1:
            continue
        published_at = (row.get("published_at") or "").strip()
        try:
            published = datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ").date()
        except ValueError:
            continue
        if (target - published).days > FIRST_DAY_VIEWS_MAX_PUBLISH_LAG_DAYS:
            continue
        ids.add(vid)
    return ids


def _generate_first_day_views_image(row: dict, today: str, *, keep_html: bool = False):
    sys.path.insert(0, str(REPO_ROOT / "collectors"))
    from comp.youtube_card import render_youtube_card, slugify, write_song_card_png

    daily = int(row["daily_views"])
    release_date_text = ""
    published_at = (row.get("published_at") or "").strip()
    if published_at:
        try:
            published_dt = datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ")
            hour_12 = published_dt.strftime("%I").lstrip("0") or "12"
            release_date_text = (
                f"{published_dt.strftime('%B %d, %Y')} · {hour_12}:{published_dt.strftime('%M %p UTC')}"
            )
        except ValueError:
            pass
    html_text = render_youtube_card(
        title=row.get("title") or row["video_id"],
        stat_label="First 24 Hours",
        stat_value=f"+{daily:,} views",
        cover_url=row.get("thumbnail_url") or "",
        footer_left="@swiftiescharts",
        badge_text="NEW VIDEO",
        release_date_text=release_date_text,
    )
    out_dir = REPO_ROOT / "snapshots" / "youtube" / "videos" / today[:4] / today[5:7] / today
    slug = slugify(row.get("title") or row["video_id"])
    out_path = out_dir / f"first_day_{slug}_{row['video_id']}.png"
    tmp_path = out_dir / f"first_day_{slug}_{row['video_id']}.html"
    return write_song_card_png(html_text, out_path, tmp_path, keep_html=keep_html)


def post_first_day_views(rows_with_daily: list[dict], existing_video_rows: list[dict], today: str, *, no_post: bool) -> None:
    """Fallback poster for "first 24h views", run at the end of every daily
    collection. The primary path is the one-off Scheduled Task created by
    _schedule_first_day_task() at discovery time (fires at published_at+24h
    with a live-fetched total) — this daily check exists purely as a safety
    net for videos whose scheduled task failed to create or to fire (e.g. PC
    off at the exact target minute with the task settings not catching up).
    Same lock file as the scheduled path, so whichever posts first wins and
    the other is a no-op.

    Uses total_views (cumulative since release), not the diffed daily_views:
    daily_views here is only the delta between the discovery snapshot and the
    next day's, which excludes any views the video already racked up before
    the collector's daily run first saw it — same undercount bug as the
    scheduled path used to have (see run_post_first_day)."""
    qualifying_ids = _first_daily_video_ids(rows_with_daily, existing_video_rows, today)
    if not qualifying_ids:
        return

    FIRST_DAY_POSTED_DIR.mkdir(parents=True, exist_ok=True)
    session_file = (
        REPO_ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "twitter_session.json"
    )

    for row in rows_with_daily:
        video_id = row["video_id"]
        if video_id not in qualifying_ids:
            continue
        lock = FIRST_DAY_POSTED_DIR / f"{video_id}.lock"
        if lock.exists():
            continue

        total_views = int(row["total_views"])
        title = row.get("title") or video_id
        image_row = {**row, "daily_views": str(total_views)}
        image_path = _generate_first_day_views_image(image_row, today)
        tweet = f'\U0001f3a5 | "{title}" debuts with {total_views:,} views in its first 24 hours on YouTube.'
        print(f"[first_day_views] Tweet: {tweet}")
        print(f"[first_day_views] Image: {image_path}")

        if no_post:
            continue
        if not session_file.exists():
            print(f"[first_day_views] ERROR: Twitter session introuvable: {session_file}")
            continue

        sys.path.insert(0, str(REPO_ROOT / "collectors" / "spotify"))
        from core.twitter import post_with_image

        if post_with_image(tweet, image_path, session_file):
            lock.write_text(f"posted {today}\n", encoding="utf-8")
        else:
            print(f"[first_day_views] Échec du post pour {title}.")


def _first_day_task_name(video_id: str) -> str:
    return f"TSM_YouTube_FirstDay_{video_id}"


def _unschedule_first_day_task(video_id: str) -> None:
    """Delete the one-off Scheduled Task for this video, if any. Safe to call
    even if no task exists (e.g. it already fired and Task Scheduler cleaned
    it up on its own, or it was posted via the daily fallback instead)."""
    task_name = _first_day_task_name(video_id)
    try:
        subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false -ErrorAction SilentlyContinue",
            ],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        pass


def _schedule_first_day_task(video_id: str, published_at: str) -> None:
    """Create a one-off Windows Scheduled Task that fires at published_at+24h
    and runs `--post-first-day <video_id>` — the "exactly 24h after upload"
    post, using a live view-count fetch at that moment rather than waiting
    for the next daily collection. If that moment has already passed (video
    discovered more than 24h after its own release — see
    FIRST_DAY_VIEWS_MAX_PUBLISH_LAG_DAYS for how late discovery is still
    tracked at all), skips the first-day post entirely: run_post_first_day
    reports the live view count as-is (see its docstring), which is only
    correct when fetched right at published_at+24h — fetching it later would
    silently include extra days of views mislabeled as "first 24 hours",
    the same kind of fake-exact-data this collector avoids for daily_views
    when a calendar day is missing."""
    published_at = (published_at or "").strip()
    try:
        published = datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        print(f"[first_day_schedule] {video_id}: published_at invalide, pas de planification.")
        return
    if (datetime.now(timezone.utc) - published).days > FIRST_DAY_VIEWS_MAX_PUBLISH_LAG_DAYS:
        return

    target_utc = published + timedelta(hours=24)
    now_utc = datetime.now(timezone.utc)
    if target_utc <= now_utc + timedelta(minutes=2):
        print(
            f"[first_day_schedule] {video_id}: fenêtre des 24h déjà passée à la découverte "
            "(video détectée trop tard) — pas de post first-day, donnée exacte impossible."
        )
        return

    target_local = target_utc.astimezone()
    at_str = target_local.strftime("%Y-%m-%dT%H:%M:%S")
    task_name = _first_day_task_name(video_id)
    python_exe = sys.executable
    ps_script = (
        f"$action = New-ScheduledTaskAction -Execute '{python_exe}' "
        f"-Argument '-m collectors.youtube.update_youtube --post-first-day {video_id}' "
        f"-WorkingDirectory '{REPO_ROOT}'; "
        f"$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date -Date '{at_str}'); "
        f"$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd "
        f"-ExecutionTimeLimit (New-TimeSpan -Minutes 15); "
        f"Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger "
        f"-Settings $settings -Force | Out-Null"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            check=True, capture_output=True, text=True, timeout=30,
        )
        print(f"[first_day_schedule] {video_id}: tâche planifiée pour {at_str} (heure locale).")
    except Exception as e:
        detail = e.stderr if isinstance(e, subprocess.CalledProcessError) else e
        print(f"[first_day_schedule] ERROR planification {video_id}: {detail}")


def run_post_first_day(video_id: str) -> int:
    """--post-first-day <video_id>: entry point for the one-off Scheduled
    Task created by _schedule_first_day_task(), which only fires this while
    the task's target time (published_at+24h) is still in the future — see
    that function for the late-discovery case. Live-fetches the current view
    count and posts it AS-IS as the "first 24h views" figure: viewCount is
    cumulative since release (starts at 0 at published_at), so a fetch done
    right at published_at+24h already IS the exact first-24h total — no
    baseline subtraction needed (a video's total_views at *discovery* time is
    NOT a t=0 baseline; the collector runs once a day, so a video can already
    have racked up a large chunk of its first-day views hours before the
    daily run first sees it — subtracting that discovery snapshot previously
    undercounted the true first-24h figure, sometimes drastically for videos
    that go viral immediately). Writes the lock file and deletes its own
    Scheduled Task regardless of outcome (a one-off task has nothing left to
    do after it fires once)."""
    if not YOUTUBE_API_KEY:
        print("[post_first_day] YOUTUBE_API_KEY manquant.")
        return 1

    existing_rows = read_csv_rows(CSV_PATH)
    recorded_row = next((r for r in existing_rows if r.get("video_id") == video_id), None)
    if not recorded_row:
        print(f"[post_first_day] {video_id}: introuvable dans le CSV.")
        _unschedule_first_day_task(video_id)
        return 1

    lock = FIRST_DAY_POSTED_DIR / f"{video_id}.lock"
    if lock.exists():
        print(f"[post_first_day] {video_id}: déjà posté (lock présent).")
        _unschedule_first_day_task(video_id)
        return 0

    stats = fetch_video_stats(YOUTUBE_API_KEY, [video_id])
    stat = stats.get(video_id)
    if not stat:
        print(
            f"[post_first_day] {video_id}: stats live indisponibles. La tâche one-off ne se "
            "redéclenchera pas — le fallback quotidien (post_first_day_views) prendra le relais."
        )
        _unschedule_first_day_task(video_id)
        return 1

    live_total = int(stat.get("viewCount", 0))
    title = stat.get("title") or recorded_row.get("title") or video_id
    row = {
        "video_id": video_id,
        "title": title,
        "daily_views": str(live_total),
        "total_views": str(live_total),
        "thumbnail_url": stat.get("thumbnailUrl") or recorded_row.get("thumbnail_url") or "",
        "published_at": stat.get("publishedAt") or recorded_row.get("published_at") or "",
    }
    image_path = _generate_first_day_views_image(row, date.today().isoformat())
    tweet = f'\U0001f3a5 | "{title}" debuts with {live_total:,} views in its first 24 hours on YouTube.'
    print(f"[post_first_day] Tweet: {tweet}")
    print(f"[post_first_day] Image: {image_path}")

    FIRST_DAY_POSTED_DIR.mkdir(parents=True, exist_ok=True)
    session_file = (
        REPO_ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "twitter_session.json"
    )
    if not session_file.exists():
        print(f"[post_first_day] ERROR: Twitter session introuvable: {session_file}")
        _unschedule_first_day_task(video_id)
        return 1

    sys.path.insert(0, str(REPO_ROOT / "collectors" / "spotify"))
    from core.twitter import post_with_image

    if post_with_image(tweet, image_path, session_file):
        lock.write_text(f"posted {date.today().isoformat()}\n", encoding="utf-8")
        print("[post_first_day] Posté avec succès.")
    else:
        print("[post_first_day] Échec du post Twitter.")

    _unschedule_first_day_task(video_id)
    return 0


def _preview_candidate_video_id(existing_rows: list[dict]) -> str | None:
    """Video id currently awaiting its first real daily_views — i.e. the one
    that will trigger post_first_day_views() on the *next* real collection.
    Picks the most recently published one if several qualify."""
    counts: dict[str, int] = {}
    last_row: dict[str, dict] = {}
    for row in existing_rows:
        vid = row.get("video_id")
        if not vid:
            continue
        counts[vid] = counts.get(vid, 0) + 1
        last_row[vid] = row
    candidates = [vid for vid, n in counts.items() if n == 1]
    if not candidates:
        return None
    candidates.sort(key=lambda vid: last_row[vid].get("published_at") or "", reverse=True)
    return candidates[0]


def run_preview() -> int:
    """--preview: render the 'first 24h views' card right now, using a live
    fetch against the video currently awaiting its first real daily_views,
    without writing the CSV or posting. The delta shown is views-so-far
    since its first snapshot, NOT the final 24h number the real run will
    compute tomorrow — it's a layout/copy preview, not a data preview."""
    if not YOUTUBE_API_KEY:
        print("[preview] YOUTUBE_API_KEY manquant.")
        return 1

    existing_rows = read_csv_rows(CSV_PATH)
    video_id = _preview_candidate_video_id(existing_rows)
    if not video_id:
        print("[preview] Aucune vidéo en attente de son 1er daily_views (toutes ont déjà >= 2 collectes).")
        return 1

    recorded_row = next(row for row in reversed(existing_rows) if row.get("video_id") == video_id)
    recorded_total = _int_or_none(recorded_row.get("total_views")) or 0
    print(f"[preview] Vidéo candidate : {recorded_row.get('title')} ({video_id})")

    stats = fetch_video_stats(YOUTUBE_API_KEY, [video_id])
    stat = stats.get(video_id)
    if not stat:
        print(f"[preview] Impossible de récupérer les stats live pour {video_id}.")
        return 1

    live_total = int(stat.get("viewCount", 0))
    delta = max(live_total - recorded_total, 0)
    row = {
        "video_id": video_id,
        "title": stat.get("title") or recorded_row.get("title") or video_id,
        "daily_views": str(delta),
        "total_views": str(live_total),
        "thumbnail_url": stat.get("thumbnailUrl") or recorded_row.get("thumbnail_url") or "",
        "published_at": stat.get("publishedAt") or recorded_row.get("published_at") or "",
    }

    image_path = _generate_first_day_views_image(row, date.today().isoformat(), keep_html=True)
    html_path = image_path.with_suffix(".html")
    tweet = f'\U0001f3a5 | "{row["title"]}" debuts with {delta:,} views in its first 24 hours on YouTube.'

    print(f"[preview] Total enregistré le {recorded_row.get('date')} : {recorded_total:,}")
    print(f"[preview] Total live actuel : {live_total:,}")
    print(f"[preview] Delta utilisé pour l'aperçu (PAS le daily_views final de demain) : +{delta:,}")
    print(f"[preview] Tweet: {tweet}")
    print(f"[preview] Image: {image_path}")
    print(f"[preview] HTML: {html_path}")
    return 0


def main() -> int:
    args = parse_args()

    if args.preview:
        return run_preview()

    if args.post_first_day:
        return run_post_first_day(args.post_first_day)

    # The scheduled run fires at 06:05 Europe/Paris ≈ 00:05 America/New_York
    # (YOUTUBE_COLLECTION_TZ), i.e. right at NY midnight. The viewCount delta
    # since the previous run therefore covers the NY calendar day that just
    # ENDED — so the data date is the run date minus one. `--date D` is taken
    # as the activity date directly (what you want the rows labelled), no shift.
    run_date = _youtube_collection_date()
    if args.date:
        activity_date = args.date
    else:
        activity_date = (date.fromisoformat(run_date) - timedelta(days=1)).isoformat()

    print(f"\n{'='*60}")
    print(f"  YouTube Views Collector — {activity_date}  (run {run_date})")
    print(f"{'='*60}\n")

    if not YOUTUBE_API_KEY:
        print("[ERROR] YOUTUBE_API_KEY manquant. Définir dans .env ou variable d'environnement.")
        print("        Voir collectors/youtube/README.md pour créer une clé Google Cloud.")
        return 1

    # ------------------------------------------------------------------
    # 1. Charger le catalogue de vidéos existant
    # ------------------------------------------------------------------
    video_db = load_video_db(VIDEO_DB_PATH)
    existing_count = len(video_db)
    print(f"[INFO] Catalogue chargé : {existing_count} vidéos connues")

    # ------------------------------------------------------------------
    # 2. Découverte de nouvelles vidéos
    # ------------------------------------------------------------------
    print("[INFO] Découverte de nouvelles vidéos sur la chaîne...")
    existing_ids = set(video_db.keys())

    if args.bootstrap:
        print("[INFO] Mode bootstrap — scan complet de la chaîne")
        new_videos = discover_new_videos(YOUTUBE_API_KEY, existing_ids)
    else:
        new_videos = discover_new_videos_short_circuit(YOUTUBE_API_KEY, existing_ids)

    new_video_ids = {v["video_id"] for v in new_videos}
    if new_videos:
        print(f"[INFO] {len(new_videos)} nouvelle(s) vidéo(s) découverte(s)")
        for v in new_videos[:5]:
            print(f"  + {v['video_id']} : {v['title'][:60]}")
        if len(new_videos) > 5:
            print(f"  ... (+{len(new_videos) - 5} autres)")
        video_db = update_video_db(video_db, new_videos)
    else:
        print("[INFO] Aucune nouvelle vidéo")

    total_videos = len(video_db)
    print(f"[INFO] Total catalogue : {total_videos} vidéos\n")

    # ------------------------------------------------------------------
    # 3. Vérifier si la date est déjà collectée
    # ------------------------------------------------------------------
    if not args.dry_run and not args.force and date_already_collected(CSV_PATH, activity_date):
        print(f"[INFO] Date {activity_date} déjà dans le CSV — skip (utiliser --date pour forcer).")
        return 0

    # ------------------------------------------------------------------
    # 4. Batch-fetch des statistiques
    # ------------------------------------------------------------------
    all_ids = list(video_db.keys())
    batches = list(chunked(all_ids, BATCH_SIZE))
    print(f"[INFO] Récupération des stats : {total_videos} vidéos en {len(batches)} batch(es)...")

    stats: dict[str, dict] = {}
    for i, chunk in enumerate(batches, 1):
        batch_stats = fetch_video_stats(YOUTUBE_API_KEY, chunk)
        stats.update(batch_stats)
        if len(batches) > 5 and i % 5 == 0:
            print(f"  ... batch {i}/{len(batches)}")

    print(f"[INFO] Stats reçues pour {len(stats)}/{total_videos} vidéos\n")

    # Horodatage exact de ce snapshot (UTC). daily_views d'une date D = delta
    # entre le snapshot_at de la ligne précédente et celui-ci — permet au
    # frontend d'afficher la fenêtre horaire réelle sur laquelle les vues ont
    # été comptées. NB : snapshot_at ≈ D+1 à 06:05 Paris (mesure prise à la fin
    # de la journée d'activité D).
    snapshot_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ------------------------------------------------------------------
    # 5. Calculer daily_views et construire les lignes CSV
    # ------------------------------------------------------------------
    existing_video_rows = read_csv_rows(CSV_PATH)
    has_prior_csv_day = has_collection_before(CSV_PATH, activity_date)
    prev_views = get_last_views(HISTORY_PATH) if has_prior_csv_day else {}
    previous_csv_date = _latest_date_before(existing_video_rows, activity_date)
    period_days = _days_between(previous_csv_date, activity_date)
    is_daily_snapshot = period_days == 1
    csv_prev_views = _last_total_views_from_csv(CSV_PATH, activity_date) if has_prior_csv_day else {}
    if csv_prev_views:
        prev_views = {**prev_views, **csv_prev_views}
    if not has_prior_csv_day:
        print("[INFO] Aucune date précédente dans le CSV — daily_views restera vide.")
    elif not is_daily_snapshot:
        label = f"{period_days}-day gain" if period_days else "period gain"
        print(f"[WARN] Date précédente: {previous_csv_date}; {activity_date} sera marqué en {label}, pas en daily.")
    new_views: dict[str, int] = {}
    rows: list[dict] = []

    for vid_id, stat in stats.items():
        total = stat.get("viewCount", 0)
        prev = prev_views.get(vid_id)
        if prev is not None:
            gain = total - prev
        elif not args.bootstrap and _is_recent_publish(stat.get("publishedAt", ""), activity_date):
            # Genuinely new release, first time this video is ever collected:
            # it didn't exist before this activity day, so its whole total_views
            # belongs to it — 0 baseline, not a blank "no data yet".
            gain = total
        else:
            gain = None
        daily = gain if is_daily_snapshot else None
        period_label = ""
        if gain is not None and period_days and period_days > 1:
            period_label = f"{period_days}-day gain"
        new_views[vid_id] = total

        rows.append(
            {
                "date": activity_date,
                "snapshot_at": snapshot_at,
                "video_id": vid_id,
                "title": stat.get("title") or video_db.get(vid_id, {}).get("title", ""),
                "rank": "",
                "previous_rank": "",
                "rank_change": "",
                "total_rank": "",
                "previous_total_rank": "",
                "total_rank_change": "",
                "published_at": stat.get("publishedAt", ""),
                "duration": stat.get("duration", ""),
                "thumbnail_url": stat.get("thumbnailUrl", ""),
                "total_views": total,
                "daily_views": daily if daily is not None else "",
                "daily_change": "",
                "daily_change_pct": "",
                "period_gain_views": gain if gain is not None and not is_daily_snapshot else "",
                "period_days": period_days if gain is not None and not is_daily_snapshot and period_days else "",
                "period_label": period_label,
                "like_count": stat.get("likeCount") if stat.get("likeCount") is not None else "",
                "comment_count": stat.get("commentCount") if stat.get("commentCount") is not None else "",
                "category_id": stat.get("categoryId", ""),
                "live_broadcast_content": stat.get("liveBroadcastContent", ""),
                "privacy_status": stat.get("privacyStatus", ""),
                "upload_status": stat.get("uploadStatus", ""),
                "tags": json.dumps(stat.get("tags") or [], ensure_ascii=False),
            }
        )

    # Tri par daily_views décroissant pour l'affichage
    rows_with_daily = [r for r in rows if r["daily_views"] != ""]
    rows_no_daily = [r for r in rows if r["daily_views"] == ""]
    rows_with_daily.sort(key=lambda r: int(r["daily_views"]), reverse=True)

    # ------------------------------------------------------------------
    # 6. Affichage Top 10
    # ------------------------------------------------------------------
    print(f"{'─'*60}")
    print(f"  Top 10 vues quotidiennes — {activity_date}")
    print(f"{'─'*60}")
    for i, r in enumerate(rows_with_daily[:10], 1):
        daily_str = f"+{_fmt_views(r['daily_views'])}" if r["daily_views"] != "" else "n/a"
        print(f"  {i:2}. {r['title'][:45]:<45}  {daily_str:>12}")
    print(f"{'─'*60}")
    print(f"  Total vidéos collectées : {len(rows)}")
    print(f"  Sans historique (1ère collecte) : {len(rows_no_daily)}\n")

    if args.dry_run:
        print("[DRY-RUN] Aucune écriture effectuée.")
        return 0

    # ------------------------------------------------------------------
    # 7. Écriture CSV + state JSON
    # ------------------------------------------------------------------
    all_rows = enrich_chart_rows(
        rows_with_daily + rows_no_daily,
        existing_rows=existing_video_rows,
        target_date=activity_date,
        key_field="video_id",
    )
    if args.force:
        removed = remove_rows_for_date(CSV_PATH, activity_date, CSV_FIELDNAMES)
        if removed:
            print(f"[INFO] {removed} ligne(s) existante(s) supprimée(s) pour {activity_date}")
    append_rows(CSV_PATH, all_rows, CSV_FIELDNAMES)
    print(f"[INFO] CSV mis à jour : {CSV_PATH}")

    # Planifie le post "first 24h views" pile à published_at+24h pour chaque
    # vidéo tout juste découverte (pas en --bootstrap : ce serait tout le
    # catalogue existant, aucune n'est "tout juste" publiée). new_video_ids est
    # capturé plus haut, avant que update_video_db ne consomme les dicts.
    if new_video_ids and not args.bootstrap and not args.no_post:
        for r in rows:
            if r["video_id"] in new_video_ids:
                try:
                    _schedule_first_day_task(r["video_id"], r.get("published_at", ""))
                except Exception as e:
                    print(f"[first_day_schedule] Échec (non bloquant) pour {r['video_id']}: {e}")

    title_rows = build_title_rows(
        date=activity_date,
        video_rows=all_rows,
        songs_path=DISCOGRAPHY_SONGS_PATH,
        manual_groups_path=VIDEO_GROUPS_PATH,
    )
    existing_title_rows = read_csv_rows(TITLE_HISTORY_PATH)
    title_rows = enrich_chart_rows(
        title_rows,
        existing_rows=existing_title_rows,
        target_date=activity_date,
        key_field="title_key",
    )
    write_title_history(
        TITLE_HISTORY_PATH,
        title_rows,
        TITLE_CSV_FIELDNAMES,
        date=activity_date,
    )
    print(f"[INFO] CSV titres mis à jour : {TITLE_HISTORY_PATH}")

    save_last_views(HISTORY_PATH, new_views)
    print(f"[INFO] State delta mis à jour : {HISTORY_PATH}")

    save_video_db(video_db, VIDEO_DB_PATH)
    print(f"[INFO] Catalogue vidéos mis à jour : {VIDEO_DB_PATH}")

    maybe_upload_youtube_to_r2(activity_date)

    # ------------------------------------------------------------------
    # 8. Post "first 24h views" card for any brand new video
    # ------------------------------------------------------------------
    try:
        post_first_day_views(rows_with_daily, existing_video_rows, activity_date, no_post=args.no_post)
    except Exception as e:
        print(f"[first_day_views] Échec (non bloquant): {e}")

    # ------------------------------------------------------------------
    # 9. Git commit/push (opt-in avec --commit)
    # ------------------------------------------------------------------
    if args.commit:
        git_commit_and_push(REPO_ROOT, message=f"youtube views {activity_date}")
    else:
        print("[INFO] Git skippé (passer --commit pour committer).")

    # ------------------------------------------------------------------
    # 10. Notification ntfy (coupée par --no-post OU --no-notify)
    # ------------------------------------------------------------------
    if not args.no_post and not args.no_notify:
        top5 = rows_with_daily[:5]
        lines = [f"YouTube Views {activity_date}", ""]
        for r in top5:
            lines.append(f"{r['title'][:40]}: +{_fmt_views(r['daily_views'])}")
        _notify(title=f"YouTube Views {activity_date}", message="\n".join(lines))
        print("[INFO] Notification envoyée.")

    print("\n[OK] Collecte terminée.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
