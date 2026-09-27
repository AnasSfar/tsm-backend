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
    python -m collectors.youtube.update_youtube --preview    # aperçu du/des post(s) first-day en attente
    python -m collectors.youtube.update_youtube --first-day-status  # releases en attente
    python -m collectors.youtube.update_youtube --first-day-cancel  # ne rien poster pour elles
    python -m collectors.youtube.update_youtube --capture-first-day VIDEO_ID  # interne (tâche +24h)
    python -m collectors.youtube.update_youtube --first-day-post    # interne (tâche de post)

Posts "first 24 hours" : voir collectors/youtube/core/first_day.py. Une vidéo
tout juste découverte est enregistrée comme en attente ; son total est
capturé EXACTEMENT à published_at+24h (tâche Planificateur one-off, ou ligne
du run quotidien si son snapshot tombe à ±15 min de ce moment) ; les vidéos
publiées ensemble forment UNE release et partent en UN post (card seule pour
1 ligne, tableau sinon, une ligne par chanson). --no-post désactive tout ça ET
la ntfy ; --no-notify ne coupe que la ntfy (c'est ce que run_youtube.bat
utilise). --bootstrap n'enregistre jamais rien (découverte en masse).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .core import first_day
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
    CHANNEL_TAGS,
    REPO_ROOT,
    TITLE_CSV_FIELDNAMES,
    TITLE_HISTORY_PATH,
    TOPIC_UPLOADS_PLAYLIST_ID,
    VIDEO_CATEGORIES_PATH,
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
from .core.title_groups import build_title_rows, video_rows_by_source, write_title_history


def _youtube_collection_tz():
    tz_name = os.getenv("YOUTUBE_COLLECTION_TZ", "America/New_York")
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        print(f"[WARN] Timezone YouTube inconnue: {tz_name!r}; fallback UTC.")
        return timezone.utc


def _youtube_collection_date() -> str:
    return datetime.now(_youtube_collection_tz()).date().isoformat()


def _published_local_date(published_at: str, tz) -> str | None:
    """Calendar day (collection timezone) a video was published on."""
    try:
        published = datetime.strptime((published_at or "").strip(), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return published.replace(tzinfo=timezone.utc).astimezone(tz).date().isoformat()


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
            "Pas de post X/Twitter : rien de la logique 'first 24 hours' "
            "(enregistrement, captures, post), ni la notification ntfy quotidienne."
        ),
    )
    p.add_argument(
        "--no-notify",
        action="store_true",
        help=(
            "Coupe uniquement la notification ntfy quotidienne. Les posts "
            "'first 24 hours' restent planifiés et postés. C'est ce que "
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
            "Aperçu (image + tweet) de chaque release first-day en attente, dans "
            "previews_and_sims/youtube-first-day/ : captures réelles si déjà prises, "
            "sinon vues live (pas le vrai chiffre +24h). N'écrit rien, ne poste pas."
        ),
    )
    p.add_argument(
        "--first-day-status",
        action="store_true",
        help="Liste les releases first-day en attente (captures, échéances, post prévu).",
    )
    p.add_argument(
        "--first-day-cancel",
        action="store_true",
        help=(
            "Ne poste rien pour les vidéos actuellement en attente (les marque traitées + "
            "supprime leurs tâches). Supprimer les tâches à la main ne suffit pas."
        ),
    )
    p.add_argument(
        "--capture-first-day",
        "--post-first-day",
        dest="capture_first_day",
        metavar="VIDEO_ID",
        default=None,
        help=(
            "Interne : capture le total exact à published_at+24h (tâche one-off créée à la "
            "découverte). Ne poste pas. --post-first-day = ancien nom, même effet."
        ),
    )
    p.add_argument(
        "--first-day-post",
        action="store_true",
        help="Interne : poste les releases prêtes (tâche planifiée +24h+15 min).",
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


FIRST_DAY_VIEWS_MAX_PUBLISH_LAG_DAYS = first_day.MAX_PUBLISH_LAG_DAYS


def _is_recent_publish(published_at: str, today: str, max_lag_days: int = FIRST_DAY_VIEWS_MAX_PUBLISH_LAG_DAYS) -> bool:
    """True if published_at is within max_lag_days of today (both UTC dates).
    Used to tell a genuinely new release (video didn't exist before today,
    so its whole total_views belongs to today) apart from an old video that
    just became publicly listed/discovered (its total_views already includes
    years of prior views — attributing all of it to today would be fake
    data)."""
    published_at = (published_at or "").strip()
    try:
        published = datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ").date()
    except ValueError:
        return False
    return (date.fromisoformat(today) - published).days <= max_lag_days


def _fetch_stats(video_ids: list[str]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for chunk in chunked(list(video_ids), BATCH_SIZE):
        stats.update(fetch_video_stats(YOUTUBE_API_KEY, chunk))
    return stats


def main() -> int:
    args = parse_args()

    now_utc = datetime.now(timezone.utc)
    if args.preview:
        if not YOUTUBE_API_KEY:
            print("[preview] YOUTUBE_API_KEY manquant.")
            return 1
        out_root = REPO_ROOT / "previews_and_sims" / "youtube-first-day" / "preview"
        return 0 if first_day.preview(now_utc, _fetch_stats, out_root) else 1

    if args.first_day_status:
        print("\n".join(first_day.status_lines(now_utc)))
        return 0

    if args.first_day_cancel:
        count = first_day.cancel_pending(now_utc)
        print(f"[first_day] {count} vidéo(s) retirée(s) de l'attente — rien ne sera posté pour elles.")
        return 0

    if args.capture_first_day:
        if not YOUTUBE_API_KEY:
            print("[first_day] YOUTUBE_API_KEY manquant.")
            return 1
        return first_day.capture_video_live(args.capture_first_day, now_utc, _fetch_stats)

    if args.first_day_post:
        results = first_day.run_tick(now_utc)
        return 1 if any(r["status"] == "failed" for r in results) else 0

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
        print("[INFO] Mode bootstrap — scan complet de la chaîne officielle")
        new_videos = discover_new_videos(YOUTUBE_API_KEY, existing_ids)
        print("[INFO] Mode bootstrap — scan complet de la chaîne Topic")
        new_videos += discover_new_videos(
            YOUTUBE_API_KEY, existing_ids | {v["video_id"] for v in new_videos},
            playlist_id=TOPIC_UPLOADS_PLAYLIST_ID,
        )
    else:
        new_videos = discover_new_videos_short_circuit(YOUTUBE_API_KEY, existing_ids)
        new_videos += discover_new_videos_short_circuit(
            YOUTUBE_API_KEY, existing_ids | {v["video_id"] for v in new_videos},
            playlist_id=TOPIC_UPLOADS_PLAYLIST_ID,
        )

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
    # Vidéos publiées APRÈS la fin du jour d'activité (entre minuit NY et ce
    # run, ~00:05 ET — l'heure des sorties de Taylor) : elles n'existaient pas
    # ce jour-là. Ni ligne CSV ni état delta pour elles ici → le run suivant les
    # voit pour la 1re fois, baseline 0, et leur jour de sortie compte TOUTES
    # leurs vues, y compris celles d'avant leur détection (fix 2026-09-27 : les
    # 27 uploads Topic d'Encore publiés à 00:01 ET le 25/09 avaient leurs
    # 0-1 114 premières vues datées du 24/09, veille de la sortie, et en moins
    # sur le 25/09). Gardées à part pour l'enregistrement first-day.
    collection_tz = _youtube_collection_tz()
    rows_after_day: list[dict] = []

    for vid_id, stat in stats.items():
        published_day = _published_local_date(stat.get("publishedAt", ""), collection_tz)
        if published_day and published_day > activity_date:
            rows_after_day.append({
                "video_id": vid_id,
                "channel": CHANNEL_TAGS.get(video_db.get(vid_id, {}).get("channel_id", ""), "main"),
                "title": stat.get("title") or video_db.get(vid_id, {}).get("title", ""),
                "published_at": stat.get("publishedAt", ""),
                "thumbnail_url": stat.get("thumbnailUrl", ""),
                "total_views": stat.get("viewCount", 0),
                "tags": json.dumps(stat.get("tags") or [], ensure_ascii=False),
            })
            continue
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
                "channel": CHANNEL_TAGS.get(video_db.get(vid_id, {}).get("channel_id", ""), "main"),
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
    print(f"  Sans historique (1ère collecte) : {len(rows_no_daily)}")
    if rows_after_day:
        print(
            f"  Publiées après la fin du {activity_date} (comptées sur le jour suivant, "
            f"vues d'avant détection incluses) : {len(rows_after_day)}"
        )
    print()

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

    # First-24h posts (core/first_day.py) : enregistre les vidéos découvertes
    # maintenant, capture celles dont le +24h tombe sur ce snapshot, planifie
    # les captures exactes +24h et le post unique de chaque release. Pas en
    # --bootstrap (tout le catalogue) ni --no-post. new_video_ids est capturé
    # plus haut, avant que update_video_db ne consomme les dicts.
    if not args.bootstrap and not args.no_post:
        try:
            first_day.handle_daily_run(
                new_rows=[r for r in rows + rows_after_day if r["video_id"] in new_video_ids],
                all_rows=rows,
                snapshot_at=datetime.fromisoformat(snapshot_at),
                now=datetime.now(timezone.utc),
            )
        except Exception as e:
            print(f"[first_day] Échec (non bloquant) : {e}")

    # Variantes de regroupement par chanson pour ce jour (colonne `source`) :
    # "all" (les 2 chaînes sommées — la vue historique, celle que lit TayBoard),
    # "videos"/"audios"/"extras" (sections de la page YouTube, classement
    # title_groups.video_category) et "main"/"topic"/"songs" (anciens toggles,
    # gardés tant que le frontend déployé peut encore les demander). Même
    # pipeline (build_title_rows + enrich_chart_rows), juste des video_rows
    # filtrés en entrée — TayBoard (source=all) est donc inchangé.
    existing_title_rows = read_csv_rows(TITLE_HISTORY_PATH)
    sources = video_rows_by_source(
        all_rows,
        songs_path=DISCOGRAPHY_SONGS_PATH,
        manual_groups_path=VIDEO_GROUPS_PATH,
        categories_path=VIDEO_CATEGORIES_PATH,
    )
    combined_title_rows: list[dict] = []
    for source_tag, source_video_rows in sources.items():
        variant_rows = build_title_rows(
            date=activity_date,
            video_rows=source_video_rows,
            songs_path=DISCOGRAPHY_SONGS_PATH,
            manual_groups_path=VIDEO_GROUPS_PATH,
            catalog_only=(source_tag == "songs"),
        )
        for r in variant_rows:
            r["source"] = source_tag
        variant_existing = [
            r for r in existing_title_rows if (r.get("source") or "all") == source_tag
        ]
        variant_rows = enrich_chart_rows(
            variant_rows,
            existing_rows=variant_existing,
            target_date=activity_date,
            key_field="title_key",
        )
        combined_title_rows.extend(variant_rows)

    write_title_history(
        TITLE_HISTORY_PATH,
        combined_title_rows,
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
    # 8. Post des releases first-day prêtes (normalement fait par leur tâche
    #    +24h+15 min ; ici = rattrapage si elle n'a pas tourné / a échoué)
    # ------------------------------------------------------------------
    if not args.no_post:
        try:
            first_day.run_tick(datetime.now(timezone.utc))
        except Exception as e:
            print(f"[first_day] Échec du post (non bloquant) : {e}")

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

    sys.path.insert(0, str(REPO_ROOT / "collectors" / "billboard"))
    from live_trigger import trigger_live_projection

    trigger_live_projection(log=print)

    print("\n[OK] Collecte terminée.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
