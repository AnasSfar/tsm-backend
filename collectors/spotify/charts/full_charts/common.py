"""Shared paths and readers for the full Spotify daily top 200 (every artist).

Raw API responses (source of truth) live under snapshots/spotify_charts_full/,
derived tables/metadata under db/spotify_charts_full/. See CONTEXTE.md of the
spotify-charts skill, section "full_charts/".
"""
from __future__ import annotations

import csv
import gzip
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
RAW_ROOT = ROOT / "snapshots" / "spotify_charts_full"
CSV_ROOT = ROOT / "db" / "spotify_charts_full"
STATE_PATH = RAW_ROOT / "state.json"
COVERS_DIR = RAW_ROOT / "covers"
# Spotify Web API track metadata (album, album type...) — reproducible, so local only.
TRACKS_META_PATH = RAW_ROOT / "meta" / "tracks.json"
# Lead-artist gender (F / NF) + source — curated, tracked by git.
ARTIST_GENDERS_PATH = CSV_ROOT / "artist_genders.json"
LEGACY_ARTISTS_CSV = ROOT / "collectors" / "spotify" / "charts" / "artists_global" / "Artists.csv"
# Explicit merges {alias_id: canonical_id} (tracked by git; *.json is ignored outside db/).
TRACK_MERGES_PATH = CSV_ROOT / "track_merges.json"
ALBUM_MERGES_PATH = CSV_ROOT / "album_merges.json"

TAYLOR_ARTIST_ID = "06HL4z0CvFAxyc27GXpf02"

CSV_FIELDS = [
    "date", "rank", "previous_rank", "peak_rank", "peak_date", "entry_rank", "entry_date",
    "days_on_chart", "streak", "streams", "entry_status", "track_id", "track_name",
    "artists", "artist_ids", "labels", "release_date", "image_url",
]


def raw_path(region: str, day: str) -> Path:
    return RAW_ROOT / region / day[:4] / f"{day}.json.gz"


def csv_path(region: str, year: str) -> Path:
    return CSV_ROOT / f"{region}_{year}.csv"


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data, *, indent: int | None = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_state() -> dict:
    state = read_json(STATE_PATH, {})
    state.setdefault("not_published", {})
    return state


def _int(v) -> int | str:
    try:
        return int(v)
    except (TypeError, ValueError):
        return ""


def entries_to_rows(day: str, payload: dict) -> list[dict]:
    rows = []
    for entry in payload.get("entries") or []:
        ced = entry.get("chartEntryData") or {}
        meta = entry.get("trackMetadata") or {}
        uri = meta.get("trackUri") or ""
        artists = meta.get("artists") or []
        rows.append({
            "date": day,
            "rank": _int(ced.get("currentRank")),
            "previous_rank": _int(ced.get("previousRank")),
            "peak_rank": _int(ced.get("peakRank")),
            "peak_date": ced.get("peakDate") or "",
            "entry_rank": _int(ced.get("entryRank")),
            "entry_date": ced.get("entryDate") or "",
            "days_on_chart": _int(ced.get("appearancesOnChart")),
            "streak": _int(ced.get("consecutiveAppearancesOnChart")),
            "streams": _int((ced.get("rankingMetric") or {}).get("value")),
            "entry_status": ced.get("entryStatus") or "",
            "track_id": uri.split(":")[-1] if uri.startswith("spotify:track:") else "",
            "track_name": (meta.get("trackName") or "").strip(),
            "artists": " | ".join(a.get("name", "") for a in artists),
            "artist_ids": " | ".join((a.get("spotifyUri") or "").split(":")[-1] for a in artists),
            "labels": " | ".join(l.get("name", "") for l in meta.get("labels") or []),
            "release_date": meta.get("releaseDate") or "",
            "image_url": meta.get("displayImageUri") or "",
        })
    return rows


def raw_regions() -> list[str]:
    if not RAW_ROOT.exists():
        return []
    return sorted(p.name for p in RAW_ROOT.iterdir() if p.is_dir() and p.name not in {"covers", "meta"})


def raw_dates(region: str, year: str) -> list[str]:
    return sorted(f.name[:10] for f in (RAW_ROOT / region / year).glob("*.json.gz"))


def read_rows(region: str, year: str) -> list[dict]:
    """Rows of the yearly table (rebuilt from raw by collect.py). Ints parsed."""
    path = csv_path(region, year)
    if not path.exists():
        raise FileNotFoundError(f"{path.relative_to(ROOT)} absent - lancer collect.py (ou --rebuild-csv-only)")
    out = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            for k in ("rank", "streams"):
                row[k] = int(row[k]) if row[k] else None
            out.append(row)
    return out


def region_title(region: str, year: str) -> str:
    """'Global', 'United States'... from the raw payload (displayChart.readableTitle)."""
    for f in sorted((RAW_ROOT / region / year).glob("*.json.gz"))[-1:]:
        with gzip.open(f, "rt", encoding="utf-8") as g:
            title = ((json.load(g).get("displayChart") or {}).get("chartMetadata") or {}).get("readableTitle") or ""
        if ":" in title:
            return title.split(":", 1)[1].strip()
    return region.upper()


def date_range(start: str, end: str) -> list[str]:
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
