#!/usr/bin/env python3
"""
fix_one.py — Manually correct the DAILY streams value for one song on one specific day,
then propagate the update through the entire data pipeline.

Usage:
    # Default: fix daily streams for that day (TOTAL is derived from previous day)
    python collectors/spotify/streams/fix_one.py "Song Title" YYYY-MM-DD NEW_DAILY_STREAMS
    python collectors/spotify/streams/fix_one.py "Song Title" YYYY-MM-DD NEW_DAILY_STREAMS --dry-run
    python collectors/spotify/streams/fix_one.py "Song Title" YYYY-MM-DD NEW_DAILY_STREAMS --track-id <SPOTIFY_ID>
    python collectors/spotify/streams/fix_one.py "Song Title" YYYY-MM-DD NEW_DAILY_STREAMS --pick 1
    python collectors/spotify/streams/fix_one.py "Song Title" YYYY-MM-DD NEW_DAILY_STREAMS --all-matches
    python collectors/spotify/streams/fix_one.py "Song Title" YYYY-MM-DD NEW_DAILY_STREAMS --no-git

    # Legacy: fix total streams directly
    python collectors/spotify/streams/fix_one.py "Song Title" YYYY-MM-DD NEW_TOTAL_STREAMS --total

What gets recomputed automatically:
    1. db/streams_history.csv
        - daily_streams value for (track, day)
        - streams for (track, day)             derived as prev_day_total + new_daily
        - daily_streams for (track, day+1)     recomputed as next_day_total - new_total (if day+1 exists)
    2. website/site/history/{day}.json and all other date JSONs (full export)
    3. website/site/data/songs.json, albums.json, etc. (full export output)
    4. R2: history-by-track/{track_id}.json — targeted single-track upload
            (enabled by default; set UPLOAD_TO_R2=0 to disable, requires boto3/credentials)

        NOTE (frontend/API):
                The React app computes day-over-day rank/daily/% changes from R2 keys:
                    - history/{YYYY-MM-DD}.json
                Therefore, when UPLOAD_TO_R2 is enabled, fix_one also uploads the corrected
                history/{day}.json and history/{day+1}.json (if present) so that the 2nd day
                reflects the fixed previous day immediately.
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
    """Return track metadata from album files + songs.json.

    Shape (best-effort):
      {track_id, title, album, edition?, type?, display_section?, display_order?, image_url?, url?}
    """
    seen: dict[str, dict] = {}

    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
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
                        seen[tid] = {
                            "track_id": tid,
                            "title": title,
                            "album": album_name,
                            "edition": (track.get("edition") or "").strip() or None,
                            "type": (track.get("type") or "").strip() or None,
                            "display_section": (track.get("display_section") or "").strip() or None,
                            "display_order": track.get("display_order"),
                            "image_url": (track.get("image_url") or "").strip() or None,
                            "url": url or None,
                        }

    if SONGS_JSON.exists():
        for section in json.loads(SONGS_JSON.read_text(encoding="utf-8-sig")):
            album = section.get("album", "")
            for track in section.get("tracks", []):
                url = (track.get("url") or track.get("spotify_url") or "").strip()
                tid = _extract_id(url)
                if not tid or tid in seen:
                    continue
                title = (track.get("title") or "").strip()
                if title:
                    seen[tid] = {
                        "track_id": tid,
                        "title": title,
                        "album": album,
                        "edition": (track.get("edition") or "").strip() or None,
                        "type": (track.get("type") or "").strip() or None,
                        "display_section": (track.get("display_section") or "").strip() or None,
                        "display_order": track.get("display_order"),
                        "image_url": (track.get("image_url") or "").strip() or None,
                        "url": url or None,
                    }
    return list(seen.values())


def _fmt_meta(t: dict) -> str:
    parts: list[str] = []
    if t.get("edition"):
        parts.append(str(t["edition"]))
    if t.get("type"):
        parts.append(str(t["type"]))
    if t.get("display_section"):
        parts.append(str(t["display_section"]))
    if t.get("display_order") not in (None, ""):
        parts.append(f"#{t['display_order']}")
    return " | ".join(parts)


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
    with HISTORY_PATH.open("r", newline="", encoding="utf-8-sig") as f:
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

def _r2_client_and_bucket():
    """Return (client, bucket) for Cloudflare R2, or (None, None) if unavailable."""
    try:
        import boto3
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        print("[R2] boto3 / python-dotenv non installés — upload ignoré.")
        return None, None

    r2_account = os.environ.get("R2_ACCOUNT_ID", "")
    r2_access = os.environ.get("R2_ACCESS_KEY_ID", "")
    r2_secret = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    r2_bucket = os.environ.get("R2_BUCKET", "")

    if not all([r2_account, r2_access, r2_secret, r2_bucket]):
        print("[R2] Variables d'environnement manquantes — upload ignoré.")
        return None, None

    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{r2_account}.r2.cloudflarestorage.com",
        aws_access_key_id=r2_access,
        aws_secret_access_key=r2_secret,
    )
    return s3, r2_bucket


def _r2_upload_history_dates(dates: list[str]) -> None:
    """Upload website/site/history/{date}.json to R2 history/{date}.json."""
    s3, bucket = _r2_client_and_bucket()
    if not s3 or not bucket:
        return

    uploaded = 0
    for d in dates:
        if not d:
            continue
        local_path = HISTORY_DIR / f"{d}.json"
        if not local_path.exists():
            continue
        raw = local_path.read_bytes()
        try:
            s3.upload_fileobj(
                io.BytesIO(raw),
                bucket,
                f"history/{d}.json",
                ExtraArgs={"ContentType": "application/json"},
            )
            uploaded += 1
            print(f"[R2] Uploadé : history/{d}.json")
        except Exception as e:
            print(f"[R2] Erreur upload history/{d}.json : {e}")

    if uploaded == 0:
        print("[R2] Aucun fichier history/{date}.json local à uploader.")


def _r2_upload_track(track_id: str) -> None:
    """Build and upload history-by-track/{track_id}.json from local history files."""
    s3, bucket = _r2_client_and_bucket()
    if not s3 or not bucket:
        return

    date_re = re.compile(r"(\d{4}-\d{2}-\d{2})")
    points: list[dict] = []

    for path in sorted(HISTORY_DIR.glob("*.json")):
        if path.name == "index.json":
            continue
        m = date_re.search(path.stem)
        if not m:
            continue
        data = json.loads(path.read_text(encoding="utf-8-sig"))
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
        s3.upload_fileobj(
            io.BytesIO(raw),
            bucket,
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
        description="Corrige manuellement le daily_streams d'une chanson pour un jour donné.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            '  python fix_one.py "Anti-Hero" 2026-03-15 1250000\n'
            '  python fix_one.py "cruel summer" 2026-03-10 980000 --dry-run\n'
            '  python fix_one.py "Blank Space" 2026-03-12 550000 --track-id 1P4mNKvhKTnqGHhiQz1zJH\n'
            '  python fix_one.py "Anti-Hero" 2026-03-15 2500000000 --total'
        ),
    )
    parser.add_argument("song",        help="Titre de la chanson (partiel ou exact, insensible à la casse)")
    parser.add_argument("day",         help="Date à corriger (YYYY-MM-DD)")
    parser.add_argument(
        "value",
        type=int,
        help=(
            "Par défaut: valeur corrigée de daily_streams pour ce jour (delta). "
            "Avec --total: valeur corrigée des streams totaux."
        ),
    )
    parser.add_argument(
        "--total",
        action="store_true",
        help="Interprète VALUE comme streams totaux (ancien comportement).",
    )
    parser.add_argument("--dry-run",   action="store_true", help="Affiche les changements sans rien écrire")
    parser.add_argument("--track-id",  metavar="ID", help="ID Spotify explicite (si le titre est ambigu)")
    parser.add_argument(
        "--pick",
        type=int,
        metavar="N",
        help=(
            "Quand plusieurs chansons correspondent au titre, sélectionne la N-ième option affichée (1-based). "
            "Utile en non-interactif."
        ),
    )
    parser.add_argument(
        "--all-matches",
        action="store_true",
        help=(
            "Quand plusieurs chansons correspondent au titre, applique la correction à toutes les options listées "
            "(utile si c'est un vrai doublon). Par défaut (sans --pick/--track-id), fix_one applique déjà à toutes."
        ),
    )
    parser.add_argument("--no-git",    action="store_true", help="Ne pas committer ni pusher")
    args = parser.parse_args()

    # ── Validate inputs ───────────────────────────────────────────────────────
    try:
        target_date = _date.fromisoformat(args.day)
    except ValueError:
        sys.exit(f"[ERREUR] Date invalide : {args.day!r}  (format attendu : YYYY-MM-DD)")

    if args.value < 0:
        sys.exit("[ERREUR] La valeur doit être positive.")

    day_str      = target_date.isoformat()
    prev_day_str = (target_date - timedelta(days=1)).isoformat()
    next_day_str = (target_date + timedelta(days=1)).isoformat()

    # Load CSV early so ambiguity output can show which IDs exist for this date.
    fieldnames, rows = load_csv()

    # ── Resolve track(s) ─────────────────────────────────────────────────────
    tracks = load_tracks()

    targets: list[dict] = []

    if args.track_id:
        track_id = args.track_id.strip()
        meta     = next((t for t in tracks if t["track_id"] == track_id), None)
        title    = meta["title"] if meta else f"(id={track_id})"
        album    = meta["album"] if meta else "?"
        targets = [{"track_id": track_id, "title": title, "album": album}]
    else:
        matches = find_tracks(args.song, tracks)
        if not matches:
            sys.exit(
                f"[ERREUR] Aucune chanson trouvée pour : {args.song!r}\n"
                "  Vérifiez le titre ou relancez avec --track-id <SPOTIFY_ID>"
            )
        if len(matches) > 1:
            print(f"[AMBIGUÏTÉ] {len(matches)} chansons correspondent à {args.song!r} :\n")
            enriched: list[dict] = []
            for t in matches:
                tid = t["track_id"]
                idx = find_row(rows, tid, day_str)
                csv_streams = None
                csv_daily = None
                if idx is not None:
                    csv_streams = (rows[idx].get("streams") or "").strip() or None
                    csv_daily = (rows[idx].get("daily_streams") or "").strip() or None
                enriched.append(
                    {
                        **t,
                        "has_csv": idx is not None,
                        "csv_streams": csv_streams,
                        "csv_daily": csv_daily,
                    }
                )

            for i, t in enumerate(enriched, start=1):
                meta = _fmt_meta(t)
                meta = f"  [{meta}]" if meta else ""
                csv_hint = ""
                if t.get("has_csv"):
                    daily = t.get("csv_daily") if t.get("csv_daily") is not None else "(vide)"
                    total = t.get("csv_streams") if t.get("csv_streams") is not None else "(vide)"
                    csv_hint = f"  csv[{day_str}]: daily={daily} total={total}"
                else:
                    csv_hint = f"  csv[{day_str}]: (absent)"
                print(f"  {i}. {t['track_id']}   {t['title']:<35}  {t['album']}{meta}{csv_hint}")

            if args.pick is not None:
                if args.pick < 1 or args.pick > len(enriched):
                    sys.exit(f"\n[ERREUR] --pick doit être entre 1 et {len(enriched)}")
                chosen = enriched[args.pick - 1]
                targets = [{"track_id": chosen["track_id"], "title": chosen["title"], "album": chosen["album"]}]
                print(f"\n→ Sélection: option {args.pick} ({chosen['track_id']})\n")
            else:
                # Default behavior on ambiguity: apply to all candidates that actually exist in the CSV for this date.
                # This matches the common "duplicate track_id" scenario without requiring an interactive prompt.
                chosen_all = [t for t in enriched if t.get("has_csv")]
                if not chosen_all:
                    sys.exit(f"\n[ERREUR] Aucune des options n'a de ligne CSV pour {day_str}.")
                targets = [{"track_id": t["track_id"], "title": t["title"], "album": t["album"]} for t in chosen_all]
                label = "toutes" if not args.all_matches else "toutes (--all-matches)"
                print(f"\n→ Sélection: {len(targets)} options ({label})\n")
        else:
            targets = [{"track_id": matches[0]["track_id"], "title": matches[0]["title"], "album": matches[0]["album"]}]

    if not targets:
        sys.exit("[ERREUR] Impossible de résoudre la chanson (cibles vides).")
    print(f"  Jour     : {day_str}")
    if args.total:
        print(f"  Mode     : total streams")
        print(f"  Valeur   : {args.value:,}")
    else:
        print(f"  Mode     : daily_streams")
        print(f"  Valeur   : {args.value:,}")
    if len(targets) == 1:
        print(f"  Chanson  : {targets[0]['title']}  ({targets[0]['album']})")
        print(f"  Track ID : {targets[0]['track_id']}")
    else:
        print(f"  Cibles   : {len(targets)} track_id")
        for t in targets:
            print(f"    - {t['track_id']}  {t['title']} ({t['album']})")
    print()

    any_change = False
    first_old_streams: int | None = None
    first_title = targets[0]["title"]

    for target in targets:
        track_id = target["track_id"]
        title = target["title"]

        # ── Locate relevant rows ─────────────────────────────────────────────
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

        old_next_daily = (rows[next_idx].get("daily_streams") or "").strip() if next_idx is not None else None

        if args.total:
            new_streams = args.value
            new_daily_str = daily_str(prev_streams, new_streams)
        else:
            new_daily = args.value
            if prev_streams is None:
                derived_prev: int | None = None

                # Fallback 0 (preferred): keep the current day's TOTAL as source of truth.
                # If we know streams(J) and we are setting daily(J)=new_daily, then:
                #   prev_total = streams(J) - new_daily
                # This allows fixing day J and recomputing day J+1 without requiring any value
                # already present on day J+1.
                if old_streams > 0 and old_streams >= new_daily:
                    derived_prev = old_streams - new_daily
                    print(
                        f"[WARN] ({track_id}) Total manquant pour {prev_day_str} — fallback via le total du jour: "
                        f"prev_total = streams({day_str}) - new_daily({day_str})."
                    )

                # Fallback A (best-effort): derive previous total from current row if daily_streams exists
                if derived_prev is None:
                    try:
                        old_daily_int = int(old_daily) if old_daily != "" else None
                    except ValueError:
                        old_daily_int = None
                    if old_daily_int is not None and old_streams >= old_daily_int:
                        derived_prev = old_streams - old_daily_int
                        print(
                            f"[WARN] ({track_id}) Total manquant pour {prev_day_str} — fallback via la ligne du jour: "
                            f"prev_total = streams({day_str}) - daily_streams({day_str})."
                        )

                # Fallback B (requested): prev_total = (next_total - next_daily) - new_daily
                if derived_prev is None and next_streams is not None and old_next_daily not in (None, ""):
                    try:
                        next_daily_int = int(old_next_daily)
                    except ValueError:
                        next_daily_int = None

                    if next_daily_int is not None and next_streams >= next_daily_int:
                        implied_curr_total = next_streams - next_daily_int
                        if implied_curr_total >= new_daily:
                            derived_prev = implied_curr_total - new_daily
                            print(
                                f"[WARN] ({track_id}) Total manquant pour {prev_day_str} — fallback via le jour suivant: "
                                f"prev_total = streams({next_day_str}) - daily({next_day_str}) - new_daily({day_str})."
                            )

                if derived_prev is None:
                    sys.exit(
                        f"[ERREUR] ({track_id}) Impossible de calculer le total pour {day_str} car la veille ({prev_day_str}) "
                        "n'a pas de total streams dans le CSV, et le fallback via le jour suivant est impossible.\n"
                        "  Assurez-vous d'avoir une ligne pour le lendemain avec daily_streams, ou relancez en mode --total."
                    )

                prev_streams = derived_prev
            new_streams = prev_streams + new_daily
            new_daily_str = str(new_daily)

        if next_streams is not None and new_streams > next_streams:
            print(
                f"[WARN] ({track_id}) Incohérence: le total du jour corrigé dépasse le total du jour suivant.\n"
                f"  {day_str} total={new_streams:,}  >  {next_day_str} total={next_streams:,}\n"
                "  Le daily_streams du lendemain sera vidé (valeur négative impossible)."
            )

        new_next_daily_str = daily_str(new_streams, next_streams) if next_streams is not None else None

        # ── Show plan ───────────────────────────────────────────────────────
        delta = new_streams - old_streams
        sign = "+" if delta >= 0 else ""
        print(f"Corrections prévues ({track_id}) :")

        if args.total:
            print(f"  [{day_str}]   streams       {old_streams:>15,}  →  {new_streams:>15,}  ({sign}{delta:,})")
        else:
            old_daily_int = int(old_daily) if old_daily.isdigit() else None
            daily_delta = None if old_daily_int is None else (args.value - old_daily_int)
            daily_sign = "+" if (daily_delta is not None and daily_delta >= 0) else ""

            print(
                f"  [{day_str}]   daily_streams {old_daily if old_daily != '' else '(vide)':>15}  "
                f"→  {new_daily_str:>15}"
                + (f"  ({daily_sign}{daily_delta:,})" if daily_delta is not None else "")
            )
            print(f"  [{day_str}]   streams       {old_streams:>15,}  →  {new_streams:>15,}  ({sign}{delta:,})  ← total recalculé")

        if args.total and old_daily != new_daily_str:
            label_old = repr(old_daily) if old_daily != "" else "(vide)"
            label_new = repr(new_daily_str) if new_daily_str != "" else "(vide)"
            print(f"  [{day_str}]   daily_streams {label_old:>15}  →  {label_new}  ← recalculé")

        if next_idx is not None and new_next_daily_str is not None \
                and old_next_daily != new_next_daily_str:
            label_old2 = repr(old_next_daily)      if old_next_daily      != "" else "(vide)"
            label_new2 = repr(new_next_daily_str)  if new_next_daily_str  != "" else "(vide)"
            print(f"  [{next_day_str}]   daily_streams {label_old2:>15}  →  {label_new2}  ← jour suivant recalculé")

        if next_idx is None:
            print(f"  [INFO] Pas de ligne CSV pour le lendemain ({next_day_str}) — day+1 ne peut pas être recalculé.")

        print()

        if first_old_streams is None:
            first_old_streams = old_streams

        if args.dry_run:
            continue

        # ── Apply CSV changes ───────────────────────────────────────────────
        rows[target_idx]["streams"]       = str(new_streams)
        rows[target_idx]["daily_streams"] = new_daily_str

        if next_idx is not None and new_next_daily_str is not None:
            rows[next_idx]["daily_streams"] = new_next_daily_str

        any_change = True

    if args.dry_run:
        print("[DRY-RUN] Aucune écriture effectuée.")
        return

    if any_change:
        save_csv(fieldnames, rows)
        print("✓ CSV mis à jour.")
    else:
        print("[INFO] Aucun changement à appliquer au CSV.")

    # ── Regenerate website data ───────────────────────────────────────────────
    print("  Regénération des fichiers site web...")
    export_for_web.export_for_web()
    print("✓ Site web régénéré.")

    # ── R2 uploads ────────────────────────────────────────────────────────────
    if os.getenv("UPLOAD_TO_R2", "").strip().lower() not in ("0", "false", "no"):
        # Update the two days used by the frontend for day-over-day comparisons.
        _r2_upload_history_dates([day_str, next_day_str])
        for t in targets:
            print(f"  Upload R2 pour {t['track_id']}...")
            _r2_upload_track(t["track_id"])
    else:
        print("[R2] Skipped (UPLOAD_TO_R2 explicitement désactivé).")

    # ── Git commit ────────────────────────────────────────────────────────────
    if not args.no_git:
        suffix = "" if len(targets) == 1 else f" (x{len(targets)} track_ids)"
        if args.total:
            msg = f"fix streams: {first_title} [{day_str}] → {args.value:,}{suffix}"
        else:
            msg = f"fix daily_streams: {first_title} [{day_str}] → {args.value:,}{suffix}"
        git_commit_and_push(_REPO_ROOT, msg)
    else:
        print("[Git] Skipped (--no-git).")

    print()
    print(f"✓ Correction appliquée pour «{first_title}» le {day_str}.")


if __name__ == "__main__":
    main()
