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
    value = (value or "").replace("�", " ")
    for quote in ("‘", "’", "ʼ", "´", "`"):
        value = value.replace(quote, "'")
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def title_key(value: str) -> str:
    return normalize_text(value).replace(" ", "_")


def _clean_video_title(value: str) -> str:
    text = re.sub(r"^\s*.*taylor\s+swift.*?\s*[-–—:]\s*", "", value or "", flags=re.I)
    def keep_feature_credit(match: re.Match[str]) -> str:
        inner = match.group(1)
        return f" {inner} " if re.search(r"\b(?:feat|ft)\.?\b", inner, flags=re.I) else " "

    text = re.sub(r"\(([^)]*)\)", keep_feature_credit, text)
    text = re.sub(r"\[([^]]*)\]", keep_feature_credit, text)
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
    with path.open(encoding="utf-8-sig") as f:
        payload = json.load(f)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("sections") or []
    return []


def _catalog_paths(path: Path) -> list[Path]:
    root = path.parent
    paths = [path, root / "features.json", root / "misc.json"]
    albums = sorted((root / "albums").glob("*.json")) if (root / "albums").exists() else []
    return [p for p in [*paths, *albums] if p.exists() and not p.name.endswith(".bak")]


def _title_aliases(track: dict[str, Any]) -> list[str]:
    family = normalize_text(str(track.get("song_family") or ""))
    version = normalize_text(str(track.get("version_tag") or ""))
    allow_base_alias = "remix" not in family and "remix" not in version
    featured_artists = [
        str(artist or "").strip()
        for artist in (track.get("featured_artists") or [])
        if str(artist or "").strip()
    ]
    requires_feature_credit = bool(featured_artists)
    filter_tags = {
        normalize_text(str(tag or ""))
        for tag in (track.get("filter_tags") or track.get("tags") or [])
    }
    is_commentary_extra = (
        "track by track" in filter_tags
        or normalize_text(str(track.get("extra_type") or "")) == "commentary"
    )
    raw = [
        track.get("title_clean"),
        None if is_commentary_extra else track.get("base_title"),
        track.get("title"),
    ]
    aliases: list[str] = []
    for value in raw:
        text = str(value or "").strip()
        if not text:
            continue
        has_feature_credit = bool(re.search(r"\b(?:feat|ft)\.?\b", text, flags=re.I))
        if requires_feature_credit and not has_feature_credit:
            for artist in featured_artists:
                aliases.append(f"{text} feat {artist}")
                aliases.append(f"{text} ft {artist}")
            continue
        aliases.append(text)
        if allow_base_alias and not requires_feature_credit:
            aliases.append(re.sub(r"\s*\([^)]*\)", "", text).strip())
            aliases.append(re.sub(r"\s*\[[^]]*\]", "", text).strip())
            aliases.append(re.sub(r"\s+feat\.?.*$", "", text, flags=re.I).strip())
    return [alias for alias in aliases if alias]


def _featureless_aliases(track: dict[str, Any]) -> list[str]:
    if not (track.get("featured_artists") or []):
        return []
    aliases: list[str] = []
    for value in (track.get("title_clean"), track.get("base_title"), track.get("title")):
        text = str(value or "").strip()
        if not text:
            continue
        text = re.sub(r"\s*\([^)]*\b(?:feat|ft)\.?\b[^)]*\)", "", text, flags=re.I).strip()
        text = re.sub(r"\s*\[[^]]*\b(?:feat|ft)\.?\b[^]]*\]", "", text, flags=re.I).strip()
        text = re.sub(r"\s+\b(?:feat|ft)\.?\b.*$", "", text, flags=re.I).strip()
        normalized = normalize_text(text)
        if normalized:
            aliases.append(normalized)
    return aliases


def load_song_catalog(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    entries: dict[str, dict[str, str]] = {}
    for catalog_path in _catalog_paths(path):
        for section in _iter_discography_sections(catalog_path):
            for track in section.get("tracks", []):
                if track.get("chart_extra") or track.get("excluded_from_public_stats"):
                    continue
                display = _track_display_title(track)
                family = str(track.get("song_family") or title_key(display))
                if not display or not family:
                    continue
                key = title_key(family)
                featureless_aliases = _featureless_aliases(track)
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
                            "featureless_aliases": "|".join(featureless_aliases),
                            "matched_catalog": "1",
                        },
                    )

    return sorted(entries.values(), key=lambda item: len(item["match_text"]), reverse=True)


def match_video_title(video_title: str, catalog: list[dict[str, str]]) -> dict[str, str]:
    cleaned = _clean_video_title(video_title)
    exact_featureless_collision = any(
        cleaned in str(entry.get("featureless_aliases") or "").split("|")
        for entry in catalog
    )
    if exact_featureless_collision and not re.search(r"\b(?:feat|ft)\.?\b", cleaned, flags=re.I):
        return {
            "title_key": f"{title_key(cleaned)}_no_feature_credit",
            "title": (cleaned or normalize_text(video_title)).title(),
            "matched_catalog": "0",
        }

    padded = f" {cleaned} "
    for entry in catalog:
        match_text = entry["match_text"]
        if match_text and f" {match_text} " in padded:
            return {
                "title_key": entry["title_key"],
                "title": entry["title"],
                "matched_catalog": "1",
            }

    fallback = cleaned
    for marker in DROP_VIDEO_MARKERS:
        fallback = fallback.replace(marker, " ")
    fallback = re.sub(r"\s+", " ", fallback).strip() or cleaned or normalize_text(video_title)
    fallback_key = title_key(fallback)
    featureless_collision = any(
        fallback_key == entry["title_key"]
        and cleaned in str(entry.get("featureless_aliases") or "").split("|")
        for entry in catalog
    )
    if featureless_collision:
        fallback_key = f"{fallback_key}_no_feature_credit"
    return {"title_key": fallback_key, "title": fallback.title(), "matched_catalog": "0"}


def load_manual_groups(path: Path) -> dict[str, dict[str, str]]:
    """Load manual video->group overrides written by the grouping editor.

    File shape: a list of {"title_key", "title", "video_ids": [...]}. Returns a
    flat video_id -> {"title_key", "title"} lookup so callers can override the
    catalog-matching result on a per-video basis without touching songs.json.
    """
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, list):
        return {}

    lookup: dict[str, dict[str, str]] = {}
    for group in payload:
        if not isinstance(group, dict):
            continue
        title_key = str(group.get("title_key") or "").strip()
        title = str(group.get("title") or "").strip()
        if not title_key or not title:
            continue
        for video_id in group.get("video_ids") or []:
            video_id = str(video_id or "").strip()
            if video_id:
                lookup[video_id] = {"title_key": title_key, "title": title}
    return lookup


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
    manual_groups_path: Path | None = None,
    catalog_only: bool = False,
) -> list[dict]:
    catalog = load_song_catalog(songs_path)
    manual_groups = load_manual_groups(manual_groups_path) if manual_groups_path else {}
    catalog_keys = {entry["title_key"] for entry in catalog}
    groups: dict[str, dict[str, Any]] = {}

    for row in video_rows:
        video_id = str(row.get("video_id") or "")
        manual = manual_groups.get(video_id)
        matched = manual or match_video_title(str(row.get("title") or ""), catalog)
        if catalog_only:
            is_catalog_match = (
                matched.get("matched_catalog") == "1"
                or (manual is not None and matched.get("title_key") in catalog_keys)
            )
            if not is_catalog_match:
                continue
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
                "snapshot_at": _shared_value(rows, "snapshot_at"),
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
                # core/gap_fill.py: "total" if any member total is estimated,
                # "daily" if only some member's previous day was.
                "estimated": next(
                    (flag for flag in ("total", "daily") if any(r.get("estimated") == flag for r in rows)), ""
                ),
            }
        )

    return sorted(out, key=lambda row: int(row["total_views"]), reverse=True)


# ---------------------------------------------------------------------------
# Video categories (owner 2026-09-27) — the YouTube page's 4 sections:
#   videos  = music videos + lyric videos/visualizers of songs (main channel)
#   audios  = Topic art tracks + main-channel audio uploads
#   extras  = trailers, announcements, lives, behind the scenes, shorts,
#             promos, commentary / track by track / voice memos
#   songs   = videos + audios (everything except extras, owner 2026-09-30;
#             replaced the old "songs" = catalog-matched titles of all rows)
#   all     = everything
# Decided from the video's own title + duration (catalog matching alone is
# wrong both ways: shorts/lives match song titles, real MVs don't match).
# Exceptions: tools/json/video_categories.json ({video_id: {"category": ...}}).
# ---------------------------------------------------------------------------

CATEGORY_VIDEOS = "videos"
CATEGORY_AUDIOS = "audios"
CATEGORY_EXTRAS = "extras"
VIDEO_CATEGORIES = (CATEGORY_VIDEOS, CATEGORY_AUDIOS, CATEGORY_EXTRAS)

_SPOKEN_RE = re.compile(r"commentary|track by track|voice memo|\binterview\b")
_MAIN_EXTRA_RE = re.compile("|".join([
    r"\blive (?:from|at|on|in)\b", r"\(live\b", r"\blive\)", r"\blive\s*$", r"\blive ?stream",
    r"performance", r"\bperforms?\b", r"behind[ -]the[ -]scenes", r"\bbts\b", r"making of", r"outtakes?",
    r"rehearsal", r"storyboard", r"vevocertified", r"\btalks?\b", r"challenge", r"diary", r"in-store",
    r"preview", r"available now", r"now available", r"out now", r"only on youtube", r"long pond",
    r"studio sessions?", r"recorded at", r"secret sessions", r"fan video", r"the collaboration",
    r"on style:", r"trailer", r"teaser", r"announcement", r"sneak peek", r"\bstation\b",
    r"karaoke", r"yule log", r"\btour\b", r"concert film", r"swiftmas", r"#shorts",
]))
_AUDIO_RE = re.compile(r"official audio|\(audio\)|\[audio\]|/ ?audio\)")
_VIDEO_MARK_RE = re.compile(r"music video|official video|lyric video|lyric version|visualizer|short film|vertical version")
# Main-channel remix/acoustic/vault uploads without a video marker are the
# label's static "official audio" uploads.
_AUDIO_HINT_RE = re.compile(r"remix|acoustic|witch version|from the vault")
_DASH_RE = re.compile(r"\s[-–—]\s")
SHORTS_MAX_SECONDS = 60


def duration_seconds(value: str) -> int | None:
    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", str(value or "").strip())
    if not match or not str(value or "").strip():
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def load_video_categories(path: Path | None) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        payload = json.load(f)
    out: dict[str, str] = {}
    for video_id, value in (payload.items() if isinstance(payload, dict) else []):
        category = value.get("category") if isinstance(value, dict) else value
        if category in VIDEO_CATEGORIES:
            out[str(video_id)] = category
    return out


def video_category(
    row: dict,
    *,
    catalog: list[dict[str, str]],
    catalog_keys: set[str],
    manual_groups: dict[str, dict[str, str]],
    overrides: dict[str, str],
) -> str:
    video_id = str(row.get("video_id") or "")
    if video_id in overrides:
        return overrides[video_id]
    title = str(row.get("title") or "").replace("’", "'").casefold()
    if row.get("channel") == "topic":
        return CATEGORY_EXTRAS if _SPOKEN_RE.search(title) else CATEGORY_AUDIOS
    seconds = duration_seconds(row.get("duration") or "")
    if seconds is not None and seconds <= SHORTS_MAX_SECONDS:
        return CATEGORY_EXTRAS
    if _SPOKEN_RE.search(title) or _MAIN_EXTRA_RE.search(title):
        return CATEGORY_EXTRAS
    if _AUDIO_RE.search(title) or (not _VIDEO_MARK_RE.search(title) and _AUDIO_HINT_RE.search(title)):
        return CATEGORY_AUDIOS
    if _DASH_RE.search(str(row.get("title") or "")):  # "Taylor Swift - Song", "ZAYN, Taylor Swift - Song"
        return CATEGORY_VIDEOS
    matched = manual_groups.get(video_id) or match_video_title(str(row.get("title") or ""), catalog)
    in_catalog = matched.get("matched_catalog") == "1" or matched.get("title_key") in catalog_keys
    return CATEGORY_VIDEOS if in_catalog else CATEGORY_EXTRAS


def video_rows_by_source(
    video_rows: list[dict],
    *,
    songs_path: Path,
    manual_groups_path: Path | None,
    categories_path: Path | None,
) -> dict[str, list[dict]]:
    """Video rows feeding each `source` of youtube_title_history.csv:
    all (TayBoard), main/topic (legacy page toggles, kept while the deployed
    frontend may still ask for them) + videos/audios/extras + songs
    (videos + audios)."""
    catalog = load_song_catalog(songs_path)
    context = {
        "catalog": catalog,
        "catalog_keys": {entry["title_key"] for entry in catalog},
        "manual_groups": load_manual_groups(manual_groups_path) if manual_groups_path else {},
        "overrides": load_video_categories(categories_path),
    }
    by_category: dict[str, list[dict]] = {category: [] for category in VIDEO_CATEGORIES}
    for row in video_rows:
        by_category[video_category(row, **context)].append(row)
    return {
        "all": video_rows,
        "main": [r for r in video_rows if (r.get("channel") or "main") == "main"],
        "topic": [r for r in video_rows if r.get("channel") == "topic"],
        **by_category,
        "songs": by_category[CATEGORY_VIDEOS] + by_category[CATEGORY_AUDIOS],
    }


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
