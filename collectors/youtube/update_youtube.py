#!/usr/bin/env python3
"""
update_youtube.py — YouTube views collector for Taylor Swift Museum.

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
import sys
from datetime import date
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
    HISTORY_PATH,
    NTFY_TOPIC,
    REPO_ROOT,
    VIDEO_DB_PATH,
    YOUTUBE_API_KEY,
)
from .core.csv_utils import (
    append_rows,
    date_already_collected,
    get_last_views,
    save_last_views,
)
from .core.git_ops import git_commit_and_push


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
    return p.parse_args()


def _fmt_views(n: int | str) -> str:
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


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
    if not args.dry_run and date_already_collected(CSV_PATH, today):
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
    prev_views = get_last_views(HISTORY_PATH)
    new_views: dict[str, int] = {}
    rows: list[dict] = []

    for vid_id, stat in stats.items():
        total = stat.get("viewCount", 0)
        prev = prev_views.get(vid_id)
        daily = (total - prev) if prev is not None else None
        new_views[vid_id] = total

        rows.append(
            {
                "date": today,
                "video_id": vid_id,
                "title": stat.get("title") or video_db.get(vid_id, {}).get("title", ""),
                "total_views": total,
                "daily_views": daily if daily is not None else "",
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
    all_rows = rows_with_daily + rows_no_daily
    append_rows(CSV_PATH, all_rows, CSV_FIELDNAMES)
    print(f"[INFO] CSV mis à jour : {CSV_PATH}")

    save_last_views(HISTORY_PATH, new_views)
    print(f"[INFO] State delta mis à jour : {HISTORY_PATH}")

    save_video_db(video_db, VIDEO_DB_PATH)
    print(f"[INFO] Catalogue vidéos mis à jour : {VIDEO_DB_PATH}")

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
