#!/usr/bin/env python3
"""
update_youtube.py — YouTube views collector for Swifties Charts.

Collecte les vues quotidiennes de toutes les vidéos de la chaîne officielle
Taylor Swift via YouTube Data API v3.

Usage:
    python -m collectors.youtube.update_youtube
    python -m collectors.youtube.update_youtube --dry-run
    python -m collectors.youtube.update_youtube --debug
    python -m collectors.youtube.update_youtube --no-post
    python -m collectors.youtube.update_youtube --date 2026-04-25
    python -m collectors.youtube.update_youtube --bootstrap  # découverte complète initiale
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

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
    VIDEO_DB_PATH,
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
        help="Pipeline complet mais sans notification ntfy.",
    )
    p.add_argument(
        "--date",
        default=None,
        help="Override la date de collecte (format YYYY-MM-DD, défaut: aujourd'hui).",
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
    if os.getenv("UPLOAD_TO_R2", "").strip().lower() in ("0", "false", "no"):
        print("[INFO] R2 upload skippé (UPLOAD_TO_R2 explicitement désactivé).")
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


def main() -> int:
    args = parse_args()
    today = args.date or date.today().isoformat()

    print(f"\n{'='*60}")
    print(f"  YouTube Views Collector — {today}")
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
    if not args.dry_run and not args.force and date_already_collected(CSV_PATH, today):
        print(f"[INFO] Date {today} déjà dans le CSV — skip (utiliser --date pour forcer).")
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

    # ------------------------------------------------------------------
    # 5. Calculer daily_views et construire les lignes CSV
    # ------------------------------------------------------------------
    existing_video_rows = read_csv_rows(CSV_PATH)
    has_prior_csv_day = has_collection_before(CSV_PATH, today)
    prev_views = get_last_views(HISTORY_PATH) if has_prior_csv_day else {}
    previous_csv_date = _latest_date_before(existing_video_rows, today)
    period_days = _days_between(previous_csv_date, today)
    is_daily_snapshot = period_days == 1
    csv_prev_views = _last_total_views_from_csv(CSV_PATH, today) if has_prior_csv_day else {}
    if csv_prev_views:
        prev_views = {**prev_views, **csv_prev_views}
    if not has_prior_csv_day:
        print("[INFO] Aucune date précédente dans le CSV — daily_views restera vide.")
    elif not is_daily_snapshot:
        label = f"{period_days}-day gain" if period_days else "period gain"
        print(f"[WARN] Date précédente: {previous_csv_date}; {today} sera marqué en {label}, pas en daily.")
    new_views: dict[str, int] = {}
    rows: list[dict] = []

    for vid_id, stat in stats.items():
        total = stat.get("viewCount", 0)
        prev = prev_views.get(vid_id)
        gain = (total - prev) if prev is not None else None
        daily = gain if is_daily_snapshot else None
        period_label = ""
        if gain is not None and period_days and period_days > 1:
            period_label = f"{period_days}-day gain"
        new_views[vid_id] = total

        rows.append(
            {
                "date": today,
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
    print(f"  Top 10 vues quotidiennes — {today}")
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
        target_date=today,
        key_field="video_id",
    )
    if args.force:
        removed = remove_rows_for_date(CSV_PATH, today, CSV_FIELDNAMES)
        if removed:
            print(f"[INFO] {removed} ligne(s) existante(s) supprimée(s) pour {today}")
    append_rows(CSV_PATH, all_rows, CSV_FIELDNAMES)
    print(f"[INFO] CSV mis à jour : {CSV_PATH}")

    title_rows = build_title_rows(
        date=today,
        video_rows=all_rows,
        songs_path=DISCOGRAPHY_SONGS_PATH,
    )
    existing_title_rows = read_csv_rows(TITLE_HISTORY_PATH)
    title_rows = enrich_chart_rows(
        title_rows,
        existing_rows=existing_title_rows,
        target_date=today,
        key_field="title_key",
    )
    write_title_history(
        TITLE_HISTORY_PATH,
        title_rows,
        TITLE_CSV_FIELDNAMES,
        date=today,
    )
    print(f"[INFO] CSV titres mis à jour : {TITLE_HISTORY_PATH}")

    save_last_views(HISTORY_PATH, new_views)
    print(f"[INFO] State delta mis à jour : {HISTORY_PATH}")

    save_video_db(video_db, VIDEO_DB_PATH)
    print(f"[INFO] Catalogue vidéos mis à jour : {VIDEO_DB_PATH}")

    maybe_upload_youtube_to_r2(today)

    # ------------------------------------------------------------------
    # 8. Git commit/push (opt-in avec --commit)
    # ------------------------------------------------------------------
    if args.commit:
        git_commit_and_push(REPO_ROOT, message=f"youtube views {today}")
    else:
        print("[INFO] Git skippé (passer --commit pour committer).")

    # ------------------------------------------------------------------
    # 9. Notification ntfy
    # ------------------------------------------------------------------
    if not args.no_post:
        top5 = rows_with_daily[:5]
        lines = [f"YouTube Views {today}", ""]
        for r in top5:
            lines.append(f"{r['title'][:40]}: +{_fmt_views(r['daily_views'])}")
        _notify(title=f"YouTube Views {today}", message="\n".join(lines))
        print("[INFO] Notification envoyée.")

    print("\n[OK] Collecte terminée.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
