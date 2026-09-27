#!/usr/bin/env python3
"""DEBUT phase gate (decision 2026-09-26).

On a release day (catalog tracks with release_date == chart date), the DEBUT
phase must go out FIRST and must never be skipped:
  whole-album tables (old tracks included, new songs marked NEW) Global,
  then US 30s later (post_album_debut_chart.py --post). The "New Songs"
  tables (--new-songs) were dropped by the owner on 2026-09-26.
Only once both succeeded is the marker `global/cards/debut_phase_done.json`
written. Every other Spotify Charts poster (worldwide/daily.py routine and
immediate posts, run_all_charts.py post steps, artists_global) checks the
marker and does NOT post while it is missing.

Usage:
  python debut_phase.py 2026-09-25            # run the phase if needed; exit 0 = done / not a debut day
  python debut_phase.py 2026-09-25 --wait 10800  # only wait for the marker (other processes)
  python debut_phase.py 2026-09-25 --check    # exit 0 if posting is allowed, 1 otherwise
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
SCRIPTS_DIR = Path(__file__).resolve().parent
SPOTIFY_ROOT = ROOT / "collectors" / "spotify"
if str(SPOTIFY_ROOT) not in sys.path:
    sys.path.insert(0, str(SPOTIFY_ROOT))

from core.data_paths import spotify_chart_dir  # noqa: E402

GLOBAL_NEW_RELEASES_SCRIPT = SCRIPTS_DIR / "post_global_new_releases.py"
ALBUM_DEBUT_SCRIPT = SCRIPTS_DIR / "post_album_debut_chart.py"
ALBUMS_DIR = ROOT / "db" / "discography" / "albums"

STEP_MAX_ATTEMPTS = 3
STEP_RETRY_SECONDS = 5
# Speed (2026-09-26): every DEBUT post takes the X account slot first
# (core/twitter.py priority queue, lower = first) over streams / Apple Music /
# artists posts that may be waiting at the same time.
DEBUT_TWITTER_PRIORITY = "0"
# DEBUT posts only (owner 2026-09-26): the full-album caption lists every
# album song on the chart (~16 lines) -> cap raised from 500 to 1000 for
# the DEBUT steps; every other post keeps the 500 default of core/twitter.py.
DEBUT_TWITTER_TEXT_LIMIT = "1000"
RUNNING_LOCK_STALE_SECONDS = 45 * 60

_debut_ids_cache: dict[str, set[str]] = {}


def _parse_date(value) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def debut_track_ids(chart_date: str) -> set[str]:
    """track_ids whose catalog release_date == chart_date (same lookup as
    post_album_debut_chart.py::find_debut_album)."""
    if chart_date in _debut_ids_cache:
        return _debut_ids_cache[chart_date]
    target = _parse_date(chart_date)
    ids: set[str] = set()
    if target is not None and ALBUMS_DIR.exists():
        for path in sorted(ALBUMS_DIR.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            for section in payload.get("sections") or []:
                for track in section.get("tracks") or []:
                    if not isinstance(track, dict) or _parse_date(track.get("release_date")) != target:
                        continue
                    tid = str(track.get("track_id") or "").strip()
                    if not tid:
                        match = re.search(r"track/([A-Za-z0-9]+)", str(track.get("url") or ""))
                        tid = match.group(1) if match else ""
                    if tid:
                        ids.add(tid)
    _debut_ids_cache[chart_date] = ids
    return ids


_album_cache: dict[str, tuple[set[str], set[str]]] = {}


def _norm_title(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def release_album_tracks(chart_date: str) -> tuple[set[str], set[str]]:
    """(track_ids, normalized titles) of EVERY track (old ones included) of
    the album(s) having a release on chart_date. Empty on normal days."""
    if chart_date in _album_cache:
        return _album_cache[chart_date]
    target = _parse_date(chart_date)
    ids: set[str] = set()
    titles: set[str] = set()
    if target is not None and ALBUMS_DIR.exists():
        for path in sorted(ALBUMS_DIR.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            tracks = [
                t for section in payload.get("sections") or [] for t in section.get("tracks") or []
                if isinstance(t, dict)
            ]
            if not any(_parse_date(t.get("release_date")) == target for t in tracks):
                continue
            for t in tracks:
                tid = str(t.get("track_id") or "").strip()
                if not tid:
                    match = re.search(r"track/([A-Za-z0-9]+)", str(t.get("url") or ""))
                    tid = match.group(1) if match else ""
                if tid:
                    ids.add(tid)
                # Former Spotify IDs of the same song: the chart can still use
                # one (incident 2026-09-26, Elizabeth Taylor 1jgTiNob5cVyXeJ3WgX5bL).
                hist = t.get("historical_track_ids") or []
                if isinstance(hist, str):
                    hist = hist.split(";")
                ids.update(str(h).strip() for h in hist if str(h).strip())
                if _norm_title(t.get("title")):
                    titles.add(_norm_title(t.get("title")))
    _album_cache[chart_date] = (ids, titles)
    return ids, titles


def is_release_album_track(chart_date: str, track_id: str | None = None, title: str | None = None) -> bool:
    """Release day anti-spam (owner 2026-09-26): a song of the released album
    (old tracks included) gets NO individual post that day (immediate
    per-country post, priority worldwide / Global RE card, first single
    region card, rank-record). It is in the DEBUT tables and the cards
    thread. Match = exact track_id or exact normalized album track title."""
    ids, titles = release_album_tracks(chart_date)
    if not ids and not titles:
        return False
    if track_id and str(track_id).strip() in ids:
        return True
    return bool(title) and _norm_title(title) in titles


def is_debut_day(chart_date: str) -> bool:
    return bool(debut_track_ids(chart_date))


def done_path(chart_date: str) -> Path:
    return spotify_chart_dir("global", chart_date) / "cards" / "debut_phase_done.json"


def _running_lock_path(chart_date: str) -> Path:
    return spotify_chart_dir("global", chart_date) / "cards" / "debut_phase.running"


def posting_allowed(chart_date: str) -> bool:
    """True when other posts may go out: not a debut day, or DEBUT done."""
    return not is_debut_day(chart_date) or done_path(chart_date).exists()


def _debut_env() -> dict[str, str]:
    return {
        **os.environ,
        "TWITTER_POST_PRIORITY": DEBUT_TWITTER_PRIORITY,
        "TWITTER_TEXT_LIMIT": DEBUT_TWITTER_TEXT_LIMIT,
    }


def _run_step(label: str, script: Path, args: list[str]) -> bool:
    for attempt in range(1, STEP_MAX_ATTEMPTS + 1):
        print(f"[DEBUT] {label} (tentative {attempt}/{STEP_MAX_ATTEMPTS})...", flush=True)
        rc = subprocess.run([sys.executable, str(script), *args], cwd=str(ROOT), env=_debut_env()).returncode
        if rc == 0:
            return True
        print(f"[WARN] [DEBUT] {label} en echec (code {rc})", flush=True)
        if attempt < STEP_MAX_ATTEMPTS:
            time.sleep(STEP_RETRY_SECONDS)
    return False


def _acquire_running_lock(chart_date: str) -> bool:
    path = _running_lock_path(chart_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            if done_path(chart_date).exists():
                return False
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:
                continue
            if age > RUNNING_LOCK_STALE_SECONDS:
                print("[WARN] [DEBUT] lock debut_phase.running perime, repris", flush=True)
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            print("[DEBUT] phase DEBUT deja en cours ailleurs, attente...", flush=True)
            time.sleep(15)


def run(chart_date: str) -> bool:
    """Runs the DEBUT phase if needed. True = posting allowed afterwards."""
    if not is_debut_day(chart_date):
        return True
    if done_path(chart_date).exists():
        print(f"[DEBUT] phase DEBUT deja faite pour {chart_date}", flush=True)
        return True
    print(f"[DEBUT] ===== Phase DEBUT {chart_date} ({len(debut_track_ids(chart_date))} titres) =====", flush=True)
    if not _acquire_running_lock(chart_date):
        return True  # finished by another process while we waited
    try:
        if done_path(chart_date).exists():
            return True
        if not _run_step("tables album debut Global + US", ALBUM_DEBUT_SCRIPT, [chart_date, "--post"]):
            print("[BLOCK] [DEBUT] tables album debut non postees -> aucun autre post", flush=True)
            return False
        path = done_path(chart_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"date": chart_date, "done_at": datetime.now().isoformat(timespec="seconds")}, indent=2),
            encoding="utf-8",
        )
        print("[DEBUT] ===== Phase DEBUT terminee =====", flush=True)
        return True
    finally:
        try:
            _running_lock_path(chart_date).unlink()
        except OSError:
            pass


def wait_done(chart_date: str, timeout_seconds: float, poll_seconds: float = 30) -> bool:
    if posting_allowed(chart_date):
        return True
    print(f"[DEBUT] jour de sortie {chart_date}: attente de la phase DEBUT (max {int(timeout_seconds)}s)...", flush=True)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        time.sleep(poll_seconds)
        if done_path(chart_date).exists():
            print("[DEBUT] phase DEBUT faite, on continue", flush=True)
            return True
    print("[BLOCK] [DEBUT] phase DEBUT toujours pas faite -> pas de post", flush=True)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="DEBUT phase gate")
    parser.add_argument("date", help="YYYY-MM-DD (chart date)")
    parser.add_argument("--wait", type=float, metavar="SECONDS", help="Only wait for the DEBUT marker")
    parser.add_argument("--check", action="store_true", help="Exit 0 if posting is allowed, 1 otherwise")
    args = parser.parse_args()
    chart_date = args.date
    if args.check:
        return 0 if posting_allowed(chart_date) else 1
    if args.wait is not None:
        return 0 if wait_done(chart_date, args.wait) else 1
    return 0 if run(chart_date) else 1


if __name__ == "__main__":
    sys.exit(main())
