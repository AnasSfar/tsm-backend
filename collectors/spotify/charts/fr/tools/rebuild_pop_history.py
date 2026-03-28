#!/usr/bin/env python3
"""
rebuild_pop_history.py — Reconstruit ts_pop_history.json depuis le début.

Pour chaque date historique :
  1. Si ts_pop_songs.csv existe → utilise pop_flag directement
  2. Sinon si ts_all_songs.csv existe → filtre les lignes pop_flag=True
  3. Sinon si log.txt existe → extrait les chansons TS du chart streaming,
     puis vérifie is_pop dans songs_db.json

Génère aussi ts_pop_history.csv (date, track_name).

Usage : python tools/rebuild_pop_history.py
"""
import csv
import json
import re
import sys
from pathlib import Path

ROOT     = Path(__file__).parent.parent          # fr/
DATA_DIR = ROOT / "data"
SONGS_DB_PATH  = ROOT / "songs_db.json"
POP_HIST_JSON  = ROOT / "ts_pop_history.json"
POP_HIST_CSV   = ROOT / "ts_pop_history.csv"
TS_NAME        = "Taylor Swift"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def load_songs_db() -> dict:
    if not SONGS_DB_PATH.exists():
        print(f"WARN: songs_db.json introuvable ({SONGS_DB_PATH})")
        return {}
    return json.loads(SONGS_DB_PATH.read_text(encoding="utf-8"))


def is_pop_in_db(db: dict, track: str) -> bool:
    key = f"taylor swift|||{norm(track)}"
    return db.get(key, {}).get("is_pop", False)


def discover_dates() -> list[str]:
    dates = []
    for year_dir in sorted(DATA_DIR.iterdir()):
        if not year_dir.is_dir() or not re.match(r"^\d{4}$", year_dir.name):
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            for day_dir in sorted(month_dir.iterdir()):
                if day_dir.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", day_dir.name):
                    dates.append(day_dir.name)
    return sorted(dates)


# ---------------------------------------------------------------------------
# Per-date extractors
# ---------------------------------------------------------------------------

def tracks_from_pop_csv(path: Path) -> list[str]:
    """Read ts_pop_songs.csv and return TS track names with pop_flag=True."""
    tracks = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            flag = str(row.get("pop_flag", "")).strip().lower()
            artist = str(row.get("artist_names", ""))
            track  = str(row.get("track_name", "")).strip()
            if flag in ("true", "1") and TS_NAME.lower() in artist.lower() and track:
                tracks.append(track)
    return tracks


def tracks_from_all_csv(path: Path) -> list[str]:
    """Read ts_all_songs.csv and return TS tracks where pop_flag=True."""
    tracks = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            flag   = str(row.get("pop_flag", "")).strip().lower()
            artist = str(row.get("artist_names", ""))
            track  = str(row.get("track_name", "")).strip()
            if flag in ("true", "1") and TS_NAME.lower() in artist.lower() and track:
                tracks.append(track)
    return tracks


# Matches:  "- #23 (-1) The Fate of Ophelia | 175 517 ..."
#       or  "- #23 (NEW) Opalite | 69 952 ..."
_LOG_SONG_RE = re.compile(r"^[-–]\s*#\d+\s*\([^)]+\)\s*(.+?)\s*\|")

# Matches pop lines in tweet.txt:  "- #37 (NEW) I Don't Wanna Live Forever"
_TWEET_POP_RE = re.compile(r"^[-–]\s*#(\d+)\s*\([^)]+\)\s*(.+)$")


def tracks_from_log(path: Path, db: dict) -> list[str]:
    """
    Parse log.txt, extract TS songs listed under the streaming section,
    then keep only those that are is_pop=True in songs_db.
    """
    text = path.read_text(encoding="utf-8")

    # Find the streaming section (between "Spotify France :" and "Spotify France (Pop) :")
    m = re.search(r"Spotify France\s*:\s*\n(.*?)(?=Spotify France \(Pop\)|$)", text, re.DOTALL)
    if not m:
        return []

    tracks = []
    for line in m.group(1).splitlines():
        lm = _LOG_SONG_RE.match(line.strip())
        if lm:
            track = lm.group(1).strip()
            if track and is_pop_in_db(db, track):
                tracks.append(track)
    return tracks


def tracks_from_tweet(path: Path) -> list[str]:
    """
    Parse tweet.txt and extract track names from the 'Spotify France (Pop) :' section.
    These are already filtered to TS pop tracks so no songs_db lookup needed.
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"Spotify France \(Pop\)\s*:\s*\n(.*?)(?:\n\n|\Z)", text, re.DOTALL)
    if not m:
        return []
    tracks = []
    for line in m.group(1).splitlines():
        lm = _TWEET_POP_RE.match(line.strip())
        if lm:
            track = lm.group(2).strip()
            if track:
                tracks.append(track)
    return tracks


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    db    = load_songs_db()
    dates = discover_dates()
    print(f"{len(dates)} dates trouvees")

    history: dict[str, list[str]] = {}  # {track_name: [date, ...]}
    csv_rows: list[tuple[str, str]] = []

    for chart_date in dates:
        day_dir = DATA_DIR / chart_date[:4] / chart_date[5:7] / chart_date

        pop_csv  = day_dir / "ts_pop_songs.csv"
        all_csv  = day_dir / "ts_all_songs.csv"
        log_txt  = day_dir / "log.txt"
        tweet_txt = day_dir / "tweet.txt"

        if pop_csv.exists():
            tracks = tracks_from_pop_csv(pop_csv)
            source = "pop_csv"
        elif all_csv.exists():
            tracks = tracks_from_all_csv(all_csv)
            source = "all_csv"
        elif tweet_txt.exists():
            tracks = tracks_from_tweet(tweet_txt)
            source = "tweet"
        elif log_txt.exists():
            tracks = tracks_from_log(log_txt, db)
            source = "log"
        else:
            continue

        if not tracks:
            continue

        for track in tracks:
            if track not in history:
                history[track] = []
            if chart_date not in history[track]:
                history[track].append(chart_date)
            csv_rows.append((chart_date, track))

        print(f"  {chart_date} [{source}] : {tracks}")

    # Sort history dates
    for track in history:
        history[track].sort()

    # Save JSON
    POP_HIST_JSON.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nts_pop_history.json sauvegarde - {len(history)} chansons")

    # Save CSV
    csv_rows.sort(key=lambda x: (x[0], x[1]))
    with open(POP_HIST_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "track_name"])
        writer.writerows(csv_rows)
    print(f"ts_pop_history.csv sauvegarde - {len(csv_rows)} lignes")

    # Summary
    print(f"\nResume :")
    for track, dates_list in sorted(history.items(), key=lambda x: x[1][0]):
        print(f"  {track}: {dates_list[0]} -> {dates_list[-1]} ({len(dates_list)} jours)")


if __name__ == "__main__":
    main()
