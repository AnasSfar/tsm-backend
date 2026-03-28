#!/usr/bin/env python3
"""
rebuild_history_from_logs.py
Reconstruit ts_history.json à partir de tous les log.txt existants.

Format attendu des lignes dans log.txt :
  #7 (+1) The Fate of Ophelia | 4 195 732 (+7.88%)
  #182 (RE) Style | 1 237 677
  (OUT) Style | last position #169   ← ignoré

Usage : python rebuild_history_from_logs.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.history import load, save, update

ROOT = Path(__file__).parent

# Regex pour une ligne de chanson :
# #<rank> (<delta>) <track> | <streams> ...
LINE_RE = re.compile(
    r"^#(?P<rank>\d{1,3})"          # #7
    r"\s+\([^)]+\)\s+"              # (+1) ou (-2) ou (RE) etc.
    r"(?P<track>.+?)"               # nom de la chanson
    r"\s+\|\s+"                     # séparateur |
    r"(?P<streams>[\d\s\u202f,]+)"  # streams (espace/virgule/narrow-space comme séparateur)
)


def clean_streams(s: str) -> int | None:
    cleaned = re.sub(r"[\s\u202f,]", "", s.strip())
    return int(cleaned) if cleaned.isdigit() else None


def parse_delta(delta_str: str, rank: int) -> int | None:
    """Déduit previous_rank depuis la chaîne delta ex: '+1', '-2', '0', 'RE'."""
    delta_str = delta_str.strip().lstrip("(").rstrip(")")
    try:
        d = int(delta_str)
        prev = rank + d          # +2 → rank amélioré de 2 → prev = rank + 2
        return prev if prev > 0 else None
    except ValueError:
        return None              # RE, NEW, -, etc.


def parse_log(log_path: Path) -> list[dict]:
    """Retourne la liste {track, rank, streams, previous_rank} pour un log.txt."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    results = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("(OUT)") or line.startswith("Taylor Swift"):
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        rank    = int(m.group("rank"))
        track   = m.group("track").strip()
        streams = clean_streams(m.group("streams"))
        if streams is None:
            continue

        # Extraire la valeur du delta depuis la ligne brute
        delta_m = re.search(r"#\d+\s+\(([^)]+)\)", line)
        prev_rank = parse_delta(delta_m.group(1), rank) if delta_m else None

        results.append({
            "track":         track,
            "rank":          rank,
            "streams":       streams,
            "previous_rank": prev_rank,
        })
    return results


def iter_source_files():
    """
    Yield (chart_date, file_path) for every date folder.
    Prefers log.txt; falls back to tweet.txt if log.txt absent.
    """
    for day_dir in sorted(ROOT.rglob("*")):
        if not day_dir.is_dir():
            continue
        chart_date = day_dir.name
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", chart_date):
            continue
        log_f   = day_dir / "log.txt"
        tweet_f = day_dir / "tweet.txt"
        if log_f.exists():
            yield chart_date, log_f
        elif tweet_f.exists():
            yield chart_date, tweet_f


def main():
    sources = list(iter_source_files())
    print(f"{len(sources)} fichiers source trouvés (log.txt ou tweet.txt)")

    history = load(ROOT / "ts_history.json")
    existing_dates = {
        date
        for entries in history.values()
        for date in entries
    }
    print(f"ts_history.json actuel : {len(history)} chansons, {len(existing_dates)} dates")

    added = 0
    skipped = 0

    for chart_date, src_path in sources:
        rows = parse_log(src_path)
        if not rows:
            skipped += 1
            continue

        for row in rows:
            update(
                history,
                row["track"],
                chart_date,
                row["rank"],
                row["streams"],
                previous_rank=row["previous_rank"],
            )
        added += 1

    save(history, ROOT / "ts_history.json")

    total_dates = len({date for entries in history.values() for date in entries})
    print(f"OK — {len(history)} chansons, {total_dates} dates dans ts_history.json")
    print(f"     {added} fichiers traités, {skipped} ignorés")


if __name__ == "__main__":
    main()
