#!/usr/bin/env python3
"""
fix_one.py — Manually correct the streams value for one song on one specific day,
then propagate the update through the entire data pipeline.

Usage:
    python collectors/spotify/streams/fix_one.py "Song Title" YYYY-MM-DD NEW_STREAMS
    python collectors/spotify/streams/fix_one.py "Song Title" YYYY-MM-DD NEW_STREAMS --dry-run
    python collectors/spotify/streams/fix_one.py "Song Title" YYYY-MM-DD NEW_STREAMS --track-id <SPOTIFY_ID>
    python collectors/spotify/streams/fix_one.py "Song Title" YYYY-MM-DD NEW_STREAMS --no-git

What gets recomputed automatically:
    1. db/streams_history.csv
          - streams value for (track, day)
          - daily_streams for (track, day)       recomputed as new - prev_day
          - daily_streams for (track, day+1)     recomputed as next_day - new
    2. website/site/history/{day}.json and all other date JSONs (full export)
    3. website/site/data/songs.json, albums.json, etc. (full export output)
    4. R2: history-by-track/{track_id}.json — targeted single-track upload
            (enabled by default; set UPLOAD_TO_R2=0 to disable, requires boto3/credentials)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import unicodedata
from datetime import date as _date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT  = _SCRIPT_DIR.parents[2]

sys.path.insert(0, str(_SCRIPT_DIR / "tools" / "scripts"))
sys.path.insert(0, str(_SCRIPT_DIR / "extras"))

import export_for_web                            # noqa: E402  (collectors/spotify/streams/extras/)
from git_ops import git_commit_and_push          # noqa: E402  (collectors/spotify/streams/tools/scripts/)

HISTORY_PATH  = _REPO_ROOT / "db" / "streams_history.csv"
ALBUMS_DIR    = _REPO_ROOT / "db" / "discography" / "albums"
SONGS_JSON    = _REPO_ROOT / "db" / "discography" / "songs.json"
HISTORY_DIR   = _REPO_ROOT / "website" / "site" / "history"

# ---------------------------------------------------------------------------
# Track discovery
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s.lower().strip())


def _extract_id(url: str) -> str:
    m = re.search(r"track/([A-Za-z0-9]+)", url or "")
    return m.group(1) if m else ""


def load_tracks() -> list[dict]:
    """Return [{track_id, title, album}] from album files + songs.json."""
    seen: dict[str, dict] = {}

    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            album_name = payload.get("album", "") if isinstance(payload, dict) else ""
            for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                for track in section.get("tracks", []):
                    url = (track.get("url") or track.get("spotify_url") or "").strip()
                    tid = _extract_id(url)
                    if not tid or tid in seen:
                        continue
                    title = (track.get("title") or "").strip()
                    if title:
                        seen[tid] = {"track_id": tid, "title": title, "album": album_name}

    if SONGS_JSON.exists():
        for section in json.loads(SONGS_JSON.read_text(encoding="utf-8")):
            album = section.get("album", "")
            for track in section.get("tracks", []):
                url = (track.get("url") or track.get("spotify_url") or "").strip()
                tid = _extract_id(url)
                if not tid or tid in seen:
                    continue
                title = (track.get("title") or "").strip()
                if title:
                    seen[tid] = {"track_id": tid, "title": title, "album": album}
    return list(seen.values())


def find_tracks(query: str, tracks: list[dict]) -> list[dict]:
    """Match tracks by title: exact first, then substring."""
    q = _norm(query)
    exact = [t for t in tracks if _norm(t["title"]) == q]
    if exact:
        return exact
    return [t for t in tracks if q in _norm(t["title"]) or _norm(t["title"]) in q]


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def load_csv() -> tuple[list[str], list[dict]]:
    if not HISTORY_PATH.exists():
        return ["date", "track_id", "streams", "daily_streams"], []
    with HISTORY_PATH.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def save_csv(fieldnames: list[str], rows: list[dict]) -> None:
    with HISTORY_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def find_row(rows: list[dict], track_id: str, day: str) -> int | None:
    for i, r in enumerate(rows):
        if (r.get("track_id") or "").strip() == track_id \
                and (r.get("date") or "").strip() == day:
            return i
    return None


def get_streams(rows: list[dict], track_id: str, day: str) -> int | None:
    idx = find_row(rows, track_id, day)
    if idx is None:
        return None
    try:
        return int((rows[idx].get("streams") or "").strip())
    except ValueError:
        return None


def daily_str(prev: int | None, curr: int) -> str:
    """Recompute daily_streams as a CSV string (empty if unknown or negative)."""
    if prev is None:
        return ""
    d = curr - prev
    return str(d) if d >= 0 else ""


# ---------------------------------------------------------------------------
# R2 targeted upload  (single track, no full rebuild)
# ---------------------------------------------------------------------------

def _r2_upload_track(track_id: str) -> None:
    """Build and upload history-by-track/{track_id}.json from local history files."""
    try:
        import boto3
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        print("[R2] boto3 / python-dotenv non installés — upload ignoré.")
        return

    r2_account  = os.environ.get("R2_ACCOUNT_ID", "")
    r2_access   = os.environ.get("R2_ACCESS_KEY_ID", "")
    r2_secret   = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    r2_bucket   = os.environ.get("R2_BUCKET", "")

    if not all([r2_account, r2_access, r2_secret, r2_bucket]):
        print("[R2] Variables d'environnement manquantes — upload ignoré.")
        return

    date_re = re.compile(r"(\d{4}-\d{2}-\d{2})")
    points: list[dict] = []

    for path in sorted(HISTORY_DIR.glob("*.json")):
        if path.name == "index.json":
            continue
        m = date_re.search(path.stem)
        if not m:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if track_id not in data:
            continue
        v = data[track_id]
        point: dict = {"date": m.group(1), "streams": v.get("s"), "daily_streams": v.get("d")}
        if "rank" in v:
            point["rank"] = v["rank"]
        points.append(point)

    if not points:
        print(f"[R2] Aucun point pour {track_id} dans les fichiers history/ — upload ignoré.")
        return

    payload = {"track_id": track_id, "points": points}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{r2_account}.r2.cloudflarestorage.com",
            aws_access_key_id=r2_access,
            aws_secret_access_key=r2_secret,
        )
        s3.upload_fileobj(
            io.BytesIO(raw),
            r2_bucket,
            f"history-by-track/{track_id}.json",
            ExtraArgs={"ContentType": "application/json"},
        )
        print(f"[R2] Uploadé : history-by-track/{track_id}.json  ({len(points)} points)")
    except Exception as e:
        print(f"[R2] Erreur lors de l'upload : {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Corrige manuellement les streams d'une chanson pour un jour donné.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            '  python fix_one.py "Anti-Hero" 2026-03-15 2500000000\n'
            '  python fix_one.py "cruel summer" 2026-03-10 1800000000 --dry-run\n'
            '  python fix_one.py "Blank Space" 2026-03-12 900000000 --track-id 1P4mNKvhKTnqGHhiQz1zJH'
        ),
    )
    parser.add_argument("song",        help="Titre de la chanson (partiel ou exact, insensible à la casse)")
    parser.add_argument("day",         help="Date à corriger (YYYY-MM-DD)")
    parser.add_argument("streams",     type=int, help="Valeur corrigée des streams TOTAUX (pas le delta)")
    parser.add_argument("--dry-run",   action="store_true", help="Affiche les changements sans rien écrire")
    parser.add_argument("--track-id",  metavar="ID", help="ID Spotify explicite (si le titre est ambigu)")
    parser.add_argument("--no-git",    action="store_true", help="Ne pas committer ni pusher")
    args = parser.parse_args()

    # ── Validate inputs ───────────────────────────────────────────────────────
    try:
        target_date = _date.fromisoformat(args.day)
    except ValueError:
        sys.exit(f"[ERREUR] Date invalide : {args.day!r}  (format attendu : YYYY-MM-DD)")

    new_streams = args.streams
    if new_streams < 0:
        sys.exit("[ERREUR] La valeur de streams doit être positive.")

    day_str      = target_date.isoformat()
    prev_day_str = (target_date - timedelta(days=1)).isoformat()
    next_day_str = (target_date + timedelta(days=1)).isoformat()

    # ── Resolve track ─────────────────────────────────────────────────────────
    tracks = load_tracks()

    if args.track_id:
        track_id = args.track_id.strip()
        meta     = next((t for t in tracks if t["track_id"] == track_id), None)
        title    = meta["title"] if meta else f"(id={track_id})"
        album    = meta["album"] if meta else "?"
    else:
        matches = find_tracks(args.song, tracks)
        if not matches:
            sys.exit(
                f"[ERREUR] Aucune chanson trouvée pour : {args.song!r}\n"
                "  Vérifiez le titre ou relancez avec --track-id <SPOTIFY_ID>"
            )
        if len(matches) > 1:
            print(f"[AMBIGUÏTÉ] {len(matches)} chansons correspondent à {args.song!r} :\n")
            for t in matches:
                print(f"  {t['track_id']}   {t['title']:<50}  {t['album']}")
            sys.exit("\nRelancez avec --track-id <ID> pour lever l'ambiguïté.")
        track_id = matches[0]["track_id"]
        title    = matches[0]["title"]
        album    = matches[0]["album"]

    print(f"  Chanson  : {title}  ({album})")
    print(f"  Track ID : {track_id}")
    print(f"  Jour     : {day_str}")
    print(f"  Valeur   : {new_streams:,}")
    print()

    # ── Load CSV and locate relevant rows ─────────────────────────────────────
    fieldnames, rows = load_csv()

    target_idx = find_row(rows, track_id, day_str)
    if target_idx is None:
        sys.exit(
            f"[ERREUR] Aucune ligne CSV pour ({track_id}, {day_str}).\n"
            "  Ce jour n'a pas encore été collecté pour cette chanson.\n"
            "  fix_one.py corrige des valeurs existantes — il n'insère pas de nouvelles lignes."
        )

    old_streams  = int((rows[target_idx].get("streams")       or "0").strip() or "0")
    old_daily    =     (rows[target_idx].get("daily_streams")  or "").strip()

    prev_streams = get_streams(rows, track_id, prev_day_str)   # may be None
    next_streams = get_streams(rows, track_id, next_day_str)   # may be None
    next_idx     = find_row(rows, track_id, next_day_str)       # may be None

    new_daily_str      = daily_str(prev_streams, new_streams)
    new_next_daily_str = daily_str(new_streams, next_streams) if next_streams is not None else None
    old_next_daily     = (rows[next_idx].get("daily_streams") or "").strip() if next_idx is not None else None

    # ── Show plan ─────────────────────────────────────────────────────────────
    delta = new_streams - old_streams
    sign  = "+" if delta >= 0 else ""
    print("Corrections prévues :")
    print(f"  [{day_str}]   streams       {old_streams:>15,}  →  {new_streams:>15,}  ({sign}{delta:,})")

    if old_daily != new_daily_str:
        label_old = repr(old_daily)    if old_daily    != "" else "(vide)"
        label_new = repr(new_daily_str) if new_daily_str != "" else "(vide)"
        print(f"  [{day_str}]   daily_streams {label_old:>15}  →  {label_new}")

    if next_idx is not None and new_next_daily_str is not None \
            and old_next_daily != new_next_daily_str:
        label_old2 = repr(old_next_daily)      if old_next_daily      != "" else "(vide)"
        label_new2 = repr(new_next_daily_str)  if new_next_daily_str  != "" else "(vide)"
        print(f"  [{next_day_str}]   daily_streams {label_old2:>15}  →  {label_new2}  ← jour suivant recalculé")

    print()

    if args.dry_run:
        print("[DRY-RUN] Aucune écriture effectuée.")
        return

    # ── Apply CSV changes ─────────────────────────────────────────────────────
    rows[target_idx]["streams"]       = str(new_streams)
    rows[target_idx]["daily_streams"] = new_daily_str

    if next_idx is not None and new_next_daily_str is not None:
        rows[next_idx]["daily_streams"] = new_next_daily_str

    save_csv(fieldnames, rows)
    print("✓ CSV mis à jour.")

    # ── Regenerate website data ───────────────────────────────────────────────
    print("  Regénération des fichiers site web...")
    export_for_web.export_for_web()
    print("✓ Site web régénéré.")

    # ── R2 targeted upload ────────────────────────────────────────────────────
    if os.getenv("UPLOAD_TO_R2", "").strip().lower() not in ("0", "false", "no"):
        print(f"  Upload R2 pour {track_id}...")
        _r2_upload_track(track_id)
    else:
        print("[R2] Skipped (UPLOAD_TO_R2 explicitement désactivé).")

    # ── Git commit ────────────────────────────────────────────────────────────
    if not args.no_git:
        msg = f"fix streams: {title} [{day_str}] {old_streams:,} → {new_streams:,}"
        git_commit_and_push(_REPO_ROOT, msg)
    else:
        print("[Git] Skipped (--no-git).")

    print()
    print(f"✓ Correction appliquée pour «{title}» le {day_str}.")


if __name__ == "__main__":
    main()
