"""Aggregate YouTube video rows into song/title-level rows."""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


DROP_VIDEO_MARKERS = (
    "official music video",
    "official video",
    "official lyric video",
    "lyric video",
    "official audio",
    "audio",
    "visualizer",
    "acoustic version",
    "acoustic",
    "remix",
    "from the vault",
    "taylor's version",
    "taylors version",
)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def title_key(value: str) -> str:
    return normalize_text(value).replace(" ", "_")


def _clean_video_title(value: str) -> str:
    text = re.sub(r"^\s*.*taylor\s+swift.*?\s*[-–—:]\s*", "", value or "", flags=re.I)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\[[^]]*\]", " ", text)
    return normalize_text(text)


def _track_display_title(track: dict[str, Any]) -> str:
    return str(
        track.get("title_clean")
        or track.get("base_title")
        or track.get("title")
        or ""
    ).strip()


def _iter_discography_sections(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        payload = json.load(f)
    return payload if isinstance(payload, list) else []


def _catalog_paths(path: Path) -> list[Path]:
    root = path.parent
    paths = [path, root / "features.json", root / "misc.json"]
    albums = sorted((root / "albums").glob("*.json")) if (root / "albums").exists() else []
    return [p for p in [*paths, *albums] if p.exists() and not p.name.endswith(".bak")]


def _title_aliases(track: dict[str, Any]) -> list[str]:
    family = normalize_text(str(track.get("song_family") or ""))
    version = normalize_text(str(track.get("version_tag") or ""))
    allow_base_alias = "remix" not in family and "remix" not in version
    raw = [
        track.get("title_clean"),
        track.get("base_title"),
        track.get("title"),
    ]
    aliases: list[str] = []
    for value in raw:
        text = str(value or "").strip()
        if not text:
            continue
        aliases.append(text)
        if allow_base_alias:
            aliases.append(re.sub(r"\s*\([^)]*\)", "", text).strip())
            aliases.append(re.sub(r"\s*\[[^]]*\]", "", text).strip())
            aliases.append(re.sub(r"\s+feat\.?.*$", "", text, flags=re.I).strip())
    return [alias for alias in aliases if alias]


def load_song_catalog(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    entries: dict[str, dict[str, str]] = {}
    for catalog_path in _catalog_paths(path):
        for section in _iter_discography_sections(catalog_path):
            for track in section.get("tracks", []):
                display = _track_display_title(track)
                family = str(track.get("song_family") or title_key(display))
                if not display or not family:
                    continue
                key = title_key(family)
                for alias in _title_aliases(track):
                    match_text = normalize_text(alias)
                    if not match_text:
                        continue
                    entries.setdefault(
                        f"{key}:{match_text}",
                        {
                            "title_key": key,
                            "title": display,
                            "match_text": match_text,
                        },
                    )

    return sorted(entries.values(), key=lambda item: len(item["match_text"]), reverse=True)


def match_video_title(video_title: str, catalog: list[dict[str, str]]) -> dict[str, str]:
    cleaned = _clean_video_title(video_title)
    padded = f" {cleaned} "
    for entry in catalog:
        match_text = entry["match_text"]
        if match_text and f" {match_text} " in padded:
            return {"title_key": entry["title_key"], "title": entry["title"]}

    fallback = cleaned
    for marker in DROP_VIDEO_MARKERS:
        fallback = fallback.replace(marker, " ")
    fallback = re.sub(r"\s+", " ", fallback).strip() or cleaned or normalize_text(video_title)
    return {"title_key": title_key(fallback), "title": fallback.title()}


def _sum_int(rows: list[dict], field: str) -> int:
    total = 0
    for row in rows:
        value = row.get(field)
        if value in ("", None):
            continue
        total += int(value)
    return total


def _sum_optional_int(rows: list[dict], field: str) -> int | str:
    values = [row.get(field) for row in rows if row.get(field) not in ("", None)]
    if not values:
        return ""
    return sum(int(value) for value in values)


def _shared_value(rows: list[dict], field: str) -> str:
    values = {str(row.get(field) or "") for row in rows if row.get(field) not in ("", None)}
    return values.pop() if len(values) == 1 else ""


def _best_group_video(rows: list[dict]) -> dict:
    def sort_key(row: dict) -> tuple[int, int]:
        daily = row.get("daily_views")
        total = row.get("total_views")
        try:
            daily_value = int(daily) if daily not in ("", None) else -1
        except (TypeError, ValueError):
            daily_value = -1
        try:
            total_value = int(total) if total not in ("", None) else 0
        except (TypeError, ValueError):
            total_value = 0
        return (daily_value, total_value)

    return max(rows, key=sort_key) if rows else {}


def build_title_rows(
    *,
    date: str,
    video_rows: list[dict],
    songs_path: Path,
) -> list[dict]:
    catalog = load_song_catalog(songs_path)
    groups: dict[str, dict[str, Any]] = {}

    for row in video_rows:
        matched = match_video_title(str(row.get("title") or ""), catalog)
        group = groups.setdefault(
            matched["title_key"],
            {
                "title_key": matched["title_key"],
                "title": matched["title"],
                "rows": [],
            },
        )
        group["rows"].append(row)

    out: list[dict] = []
    for group in groups.values():
        rows = group["rows"]
        primary = _best_group_video(rows)
        out.append(
            {
                "date": date,
                "title_key": group["title_key"],
                "title": group["title"],
                "video_count": len(rows),
                "thumbnail_url": primary.get("thumbnail_url", ""),
                "primary_video_id": primary.get("video_id", ""),
                "total_views": _sum_int(rows, "total_views"),
                "daily_views": _sum_optional_int(rows, "daily_views"),
                "period_gain_views": _sum_optional_int(rows, "period_gain_views"),
                "period_days": _shared_value(rows, "period_days"),
                "period_label": _shared_value(rows, "period_label"),
                "like_count": _sum_optional_int(rows, "like_count"),
                "comment_count": _sum_optional_int(rows, "comment_count"),
                "video_ids": json.dumps([row.get("video_id", "") for row in rows], ensure_ascii=False),
                "video_titles": json.dumps([row.get("title", "") for row in rows], ensure_ascii=False),
            }
        )

    return sorted(out, key=lambda row: int(row["total_views"]), reverse=True)


def write_title_history(
    path: Path,
    rows: list[dict],
    fieldnames: list[str],
    *,
    date: str,
) -> None:
    existing: list[dict] = []
    if path.exists() and path.stat().st_size:
        with path.open(newline="", encoding="utf-8-sig") as f:
            existing = [row for row in csv.DictReader(f) if row.get("date") != date]

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing)
        writer.writerows(rows)
