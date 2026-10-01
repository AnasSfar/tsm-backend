"""First-24h YouTube debut posts — one post per release, exact captures only.

Rewritten 2026-09-27 after "The Life of a Showgirl: The Encore": the Topic
channel re-uploaded the whole deluxe (2 uploads per song, old songs included),
then the lyric videos came the next day, and the old per-video logic posted
~34 separate cards — duplicates, re-uploads of year-old songs presented as
debuts, and fallback posts measured 28h/52h after upload but labelled "first
24 hours".

Three decoupled steps:

1. REGISTER (daily run, at discovery): a newly discovered video published less
   than MAX_PUBLISH_LAG_DAYS ago gets ``first_day/pending/<id>.json``.
2. CAPTURE (exact, time-critical): its first-24h figure is its cumulative
   viewCount read within CAPTURE_TOLERANCE of ``published_at + 24h`` — by the
   one-off Scheduled Task fired at that instant (``capture_video_live``), or by
   a daily-run row whose ``snapshot_at`` lands in the window
   (``capture_from_daily_rows``). Frozen in ``first_day/captures/<id>.json``.
   Outside the window nothing is captured: the video is dropped, never posted
   with a longer window labelled "24 hours".
3. POST (not time-critical, ``run_tick``): pending videos published within
   RELEASE_GAP of each other form ONE release. Once no member can still be
   captured and POST_GRACE has passed its last due time, it is posted: Topic
   audios and main-channel videos are never mixed — one post each, a 2-post
   thread when the release has both. Rows are songs (uploads of the same song
   summed within a post, e.g. the 2 Topic uploads); a Topic upload of a song
   that already had a YouTube video is a re-upload, not a debut, and is left
   out. 1 row -> the single video card, >= 2 rows -> a table.

The figure is the video's cumulative viewCount at +24h, so the views it got
before the collector first saw it (a few seconds/minutes after release) are
always included.

Every resolved release (posted / skipped / cancelled) is archived in
``first_day/releases/<anchor>.json`` and its members get the historical
``first_day_posted/<id>.lock`` so they are never considered again.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .config import (
    DISCOGRAPHY_SONGS_PATH,
    REPO_ROOT,
    TOOLS_JSON_DIR,
    VIDEO_DB_PATH,
    VIDEO_GROUPS_PATH,
)
from .title_groups import (
    _iter_discography_sections,
    _track_display_title,
    load_manual_groups,
    load_song_catalog,
    match_video_title,
    normalize_text,
    title_key,
)

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIRST_DAY_DIR = TOOLS_JSON_DIR / "first_day"
PENDING_DIR = FIRST_DAY_DIR / "pending"
CAPTURES_DIR = FIRST_DAY_DIR / "captures"
RELEASES_DIR = FIRST_DAY_DIR / "releases"
POSTED_LOCK_DIR = TOOLS_JSON_DIR / "first_day_posted"
SNAPSHOTS_DIR = REPO_ROOT / "snapshots" / "youtube" / "videos"
ALBUMS_DIR = REPO_ROOT / "db" / "discography" / "albums"
TWITTER_SESSION = (
    REPO_ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "twitter_session.json"
)

FIRST_DAY = timedelta(hours=24)
MAX_PUBLISH_LAG_DAYS = 4
# |capture instant - (published_at + 24h)| allowed. The daily run fires at
# ~00:05 ET, i.e. ~5 min after a midnight-ET release's 24h mark.
CAPTURE_TOLERANCE = timedelta(minutes=15)
# The capture task starts this early and waits in-process for the exact 24h
# second, so Task Scheduler / Python start-up latency never delays the read.
CAPTURE_LEAD = timedelta(minutes=3)
# Uploads published within this gap of each other = one release (the Encore
# Topic uploads spread over 04:01-04:05 UTC; its lyric videos came 20h later).
RELEASE_GAP = timedelta(hours=2)
# Wait after the last member's 24h mark before posting, so uploads the daily
# run only discovers at that moment still join the release.
POST_GRACE = timedelta(minutes=15)
# A "first 24 hours" post more than 2 days after the fact is stale: skipped.
POST_MAX_DELAY = timedelta(hours=48)
HANDLE = "@swiftiescharts"

KIND_AUDIO = "Official Audio"
# Topic (auto-generated audio) and main-channel videos are never mixed: one
# table each, posted as a 2-post thread when a release has both (owner
# 2026-09-27). Thread order = this order.
GROUP_AUDIO = "audio"
GROUP_VIDEO = "video"
GROUP_ORDER = (GROUP_AUDIO, GROUP_VIDEO)
VIDEO_PLURAL = {
    "Lyric Video": "lyric videos",
    "Music Video": "music videos",
    "Visualizer": "visualizers",
    KIND_AUDIO: "official audio videos",
    "Video": "videos",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _int(value: object) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _handled_ids() -> set[str]:
    if not POSTED_LOCK_DIR.exists():
        return set()
    return {p.stem for p in POSTED_LOCK_DIR.glob("*.lock")}


def upload_kind(channel: str, title: str) -> str:
    if channel == "topic":
        return KIND_AUDIO
    text = normalize_text(title)
    if "lyric video" in text or "lyric visualizer" in text:
        return "Lyric Video"
    if "music video" in text or "official video" in text:
        return "Music Video"
    if "visualizer" in text:
        return "Visualizer"
    if "official audio" in text:
        return KIND_AUDIO
    return "Video"


def _release_time_text(published: datetime) -> str:
    hour = published.strftime("%I").lstrip("0") or "12"
    return f"{published.strftime('%B')} {published.day}, {published.year} · {hour}:{published.strftime('%M %p')} UTC"


# ---------------------------------------------------------------------------
# Pending registry + captures
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    video_id: str
    title: str
    channel: str
    published_at: datetime
    thumbnail_url: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def due(self) -> datetime:
        return self.published_at + FIRST_DAY

    def to_json(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "channel": self.channel,
            "published_at": _iso(self.published_at),
            "due_at": _iso(self.due),
            "thumbnail_url": self.thumbnail_url,
            "tags": self.tags,
        }

    @classmethod
    def from_json(cls, data: dict | None) -> "Candidate | None":
        if not isinstance(data, dict):
            return None
        published = parse_utc(data.get("published_at"))
        video_id = str(data.get("video_id") or "")
        if not video_id or published is None:
            return None
        return cls(
            video_id=video_id,
            title=str(data.get("title") or video_id),
            channel=str(data.get("channel") or "main"),
            published_at=published,
            thumbnail_url=str(data.get("thumbnail_url") or ""),
            tags=[str(t) for t in (data.get("tags") or [])],
        )


def register_candidates(rows: list[dict], now: datetime) -> list[Candidate]:
    """rows: CSV-shaped rows (video_id, title, channel, published_at,
    thumbnail_url, tags) of the videos THIS run discovered."""
    handled = _handled_ids()
    registered: list[Candidate] = []
    for row in rows:
        video_id = str(row.get("video_id") or "")
        published = parse_utc(row.get("published_at"))
        if not video_id or published is None or video_id in handled:
            continue
        if now - published > timedelta(days=MAX_PUBLISH_LAG_DAYS):
            continue
        path = PENDING_DIR / f"{video_id}.json"
        if path.exists():
            continue
        tags = row.get("tags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except ValueError:
                tags = []
        candidate = Candidate(
            video_id=video_id,
            title=str(row.get("title") or video_id),
            channel=str(row.get("channel") or "main"),
            published_at=published,
            thumbnail_url=str(row.get("thumbnail_url") or ""),
            tags=[str(t) for t in tags] if isinstance(tags, list) else [],
        )
        _write_json(path, candidate.to_json())
        registered.append(candidate)
    return registered


def load_pending() -> dict[str, Candidate]:
    handled = _handled_ids()
    out: dict[str, Candidate] = {}
    if not PENDING_DIR.exists():
        return out
    for path in PENDING_DIR.glob("*.json"):
        candidate = Candidate.from_json(_read_json(path))
        if candidate and candidate.video_id not in handled:
            out[candidate.video_id] = candidate
    return out


def load_captures() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not CAPTURES_DIR.exists():
        return out
    for path in CAPTURES_DIR.glob("*.json"):
        data = _read_json(path)
        if isinstance(data, dict) and _int(data.get("views")) is not None:
            out[path.stem] = data
    return out


def _write_capture(candidate: Candidate, views: int, captured_at: datetime, source: str) -> bool:
    path = CAPTURES_DIR / f"{candidate.video_id}.json"
    if path.exists():
        return False  # frozen: first exact capture wins
    _write_json(path, {
        "video_id": candidate.video_id,
        "views": int(views),
        "captured_at": _iso(captured_at),
        "due_at": _iso(candidate.due),
        "offset_seconds": int((captured_at - candidate.due).total_seconds()),
        "source": source,
    })
    return True


def capture_from_daily_rows(rows: list[dict], snapshot_at: datetime) -> list[str]:
    """Daily-run capture: a pending video whose 24h mark falls within
    CAPTURE_TOLERANCE of this run's snapshot gets this row's total_views."""
    pending = load_pending()
    captures = load_captures()
    captured: list[str] = []
    for row in rows:
        candidate = pending.get(str(row.get("video_id") or ""))
        if candidate is None or candidate.video_id in captures:
            continue
        if abs(snapshot_at - candidate.due) > CAPTURE_TOLERANCE:
            continue
        views = _int(row.get("total_views"))
        if views is not None and _write_capture(candidate, views, snapshot_at, "daily_run"):
            captured.append(candidate.video_id)
    return captured


def capture_video_live(
    video_id: str,
    now: datetime,
    fetch_stats: Callable[[list[str]], dict[str, dict]],
    *,
    log: Callable[[str], None] = print,
    clock: Callable[[], datetime] | None = None,
) -> int:
    """--capture-first-day <id>: entry point of the one-off Scheduled Task
    fired CAPTURE_LEAD before published_at + 24h. With a clock, waits for the
    exact 24h second before reading. Captures only; posting is run_tick's job."""
    unregister_task(capture_task_name(video_id))
    candidate = load_pending().get(video_id)
    if candidate is None:
        log(f"[first_day] {video_id}: pas en attente (déjà traité ou jamais enregistré).")
        return 0
    if video_id in load_captures():
        log(f"[first_day] {video_id}: déjà capturé.")
        return 0
    offset = now - candidate.due
    if abs(offset) > CAPTURE_TOLERANCE:
        log(
            f"[first_day] {video_id}: lancé à {int(offset.total_seconds() // 60)} min de published_at+24h "
            f"(tolérance {int(CAPTURE_TOLERANCE.total_seconds() // 60)} min) — pas de capture, "
            "un total pris à ce moment ne serait pas « 24 heures »."
        )
        return 0
    if clock is not None and now < candidate.due:
        log(f"[first_day] {video_id}: attente de published_at+24h ({_iso(candidate.due)}).")
        while (remaining := (candidate.due - clock()).total_seconds()) > 0:
            time.sleep(min(remaining, 5.0))
        now = clock()
    stat = (fetch_stats([video_id]) or {}).get(video_id)
    if not stat:
        log(f"[first_day] {video_id}: stats live indisponibles — le run quotidien tentera sa propre capture.")
        return 1
    views = int(stat.get("viewCount", 0))
    _write_capture(candidate, views, now, "scheduled_task")
    log(f"[first_day] {video_id}: {views:,} vues capturées à +24h ({candidate.title}).")
    return 0


# ---------------------------------------------------------------------------
# Releases
# ---------------------------------------------------------------------------

@dataclass
class Release:
    members: list[Candidate]

    @property
    def anchor(self) -> str:
        return self.members[0].video_id

    @property
    def start(self) -> datetime:
        return self.members[0].published_at

    @property
    def last_due(self) -> datetime:
        return max(m.due for m in self.members)

    @property
    def post_at(self) -> datetime:
        return self.last_due + POST_GRACE

    def states(self, captures: dict[str, dict], now: datetime) -> dict[str, str]:
        out: dict[str, str] = {}
        for member in self.members:
            if member.video_id in captures:
                out[member.video_id] = "captured"
            elif now <= member.due + CAPTURE_TOLERANCE:
                out[member.video_id] = "waiting"
            else:
                out[member.video_id] = "expired"
        return out


def group_releases(pending: dict[str, Candidate]) -> list[Release]:
    ordered = sorted(pending.values(), key=lambda c: (c.published_at, c.video_id))
    groups: list[list[Candidate]] = []
    for candidate in ordered:
        if groups and candidate.published_at - groups[-1][-1].published_at <= RELEASE_GAP:
            groups[-1].append(candidate)
        else:
            groups.append([candidate])
    return [Release(members=group) for group in groups]


@dataclass
class DebutRow:
    key: str
    title: str
    is_song: bool
    group: str
    uploads: list[dict] = field(default_factory=list)

    @property
    def views(self) -> int:
        return sum(int(u["views"]) for u in self.uploads)

    @property
    def primary(self) -> dict:
        return max(self.uploads, key=lambda u: int(u["views"]))

    @property
    def kinds(self) -> list[str]:
        kinds: list[str] = []
        for upload in sorted(self.uploads, key=lambda u: -int(u["views"])):
            if upload["kind"] not in kinds:
                kinds.append(upload["kind"])
        return kinds

    @property
    def subtitle(self) -> str:
        label = " + ".join(self.kinds)
        if len(self.uploads) > 1:
            label += f" · {len(self.uploads)} uploads"
        return label


@dataclass
class Section:
    """One post: the release's Topic audios, or its main-channel videos."""
    group: str
    rows: list[DebutRow]
    album: str | None = None
    tweet: str = ""

    @property
    def what(self) -> str:
        if self.group == GROUP_AUDIO:
            return "new songs"  # Topic rows only ever hold songs making their debut
        kinds = {kind for row in self.rows for kind in row.kinds}
        return VIDEO_PLURAL.get(next(iter(kinds)), "videos") if len(kinds) == 1 else "new videos"

    @property
    def start(self) -> datetime:
        return min(parse_utc(u["published_at"]) for row in self.rows for u in row.uploads)


@dataclass
class Plan:
    release: Release
    sections: list[Section]
    notes: dict[str, str]

    @property
    def rows(self) -> list[DebutRow]:
        return [row for section in self.sections for row in section.rows]


@dataclass
class _Context:
    catalog: list[dict]
    catalog_keys: set[str]
    manual: dict[str, dict]
    album_map: dict[str, set[str]]
    known: dict[str, tuple[str, datetime, bool]]  # video_id -> (title, published, precise)


def _album_map() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in sorted(ALBUMS_DIR.glob("*.json")):
        payload = _read_json(path)
        album = str(payload.get("album") or "").strip() if isinstance(payload, dict) else ""
        if not album:
            continue
        for section in _iter_discography_sections(path):
            for track in section.get("tracks", []):
                if track.get("chart_extra") or track.get("excluded_from_public_stats"):
                    continue
                display = _track_display_title(track)
                family = str(track.get("song_family") or title_key(display))
                if family:
                    out.setdefault(title_key(family), set()).add(album)
    return out


def _load_context(pending: dict[str, Candidate]) -> _Context:
    catalog = load_song_catalog(DISCOGRAPHY_SONGS_PATH)
    known: dict[str, tuple[str, datetime, bool]] = {}
    video_db = _read_json(VIDEO_DB_PATH) or {}
    for video_id, info in video_db.items():
        if not isinstance(info, dict):
            continue
        # video_db only keeps the upload DATE (playlist item date).
        published = parse_utc(f"{str(info.get('published_at') or '')[:10]}T00:00:00Z")
        if published is not None:
            known[video_id] = (str(info.get("title") or ""), published, False)
    for record_path in RELEASES_DIR.glob("*.json") if RELEASES_DIR.exists() else []:
        record = _read_json(record_path) or {}
        for member in record.get("members") or []:
            published = parse_utc(member.get("published_at"))
            if published is not None and member.get("video_id"):
                known[member["video_id"]] = (str(member.get("title") or ""), published, True)
    for candidate in pending.values():
        known[candidate.video_id] = (candidate.title, candidate.published_at, True)
    return _Context(
        catalog=catalog,
        catalog_keys={entry["title_key"] for entry in catalog},
        manual=load_manual_groups(VIDEO_GROUPS_PATH),
        album_map=_album_map(),
        known=known,
    )


def _match(video_id: str, title: str, ctx: _Context) -> dict[str, str]:
    manual = ctx.manual.get(video_id)
    if manual:
        return {**manual, "matched_catalog": "1" if manual["title_key"] in ctx.catalog_keys else "0"}
    return match_video_title(title, ctx.catalog)


def _has_prior_upload(key: str, display_title: str, release: Release, ctx: _Context) -> bool:
    """True when a video outside this release, published before it, already
    belongs to the same song — i.e. the song had its YouTube debut earlier."""
    members = {m.video_id for m in release.members}
    needle = normalize_text(display_title)
    for video_id, (title, published, precise) in ctx.known.items():
        if video_id in members:
            continue
        if precise and published >= release.start:
            continue
        if not precise and published.date() >= release.start.date():
            continue
        if needle and video_id not in ctx.manual and needle not in normalize_text(title):
            continue
        if _match(video_id, title, ctx).get("title_key") == key:
            return True
    return False


def _release_album(rows: list[DebutRow], release: Release, ctx: _Context) -> str | None:
    if not rows or not all(row.is_song for row in rows):
        return None
    album_sets = [ctx.album_map.get(row.key.removesuffix("_no_feature_credit")) for row in rows]
    if all(album_sets):
        common = set.intersection(*album_sets)
        if len(common) == 1:
            from collectors.comp.discography import display_title_for_album

            return display_title_for_album(common.pop())
    # Songs not in the catalog yet (release day, before the backfill): Topic
    # uploads carry the album name in their tags.
    by_id = {m.video_id: m for m in release.members}
    tag_sets = [
        set(by_id[u["video_id"]].tags)
        for row in rows for u in row.uploads
        if u["channel"] == "topic" and by_id.get(u["video_id"]) and by_id[u["video_id"]].tags
    ]
    if not tag_sets or len(tag_sets) != sum(len(row.uploads) for row in rows):
        return None
    titles = {normalize_text(u["title"]) for row in rows for u in row.uploads}
    common_tags = {
        tag for tag in set.intersection(*tag_sets)
        if "taylor" not in tag.casefold() and "テイラー" not in tag and normalize_text(tag) not in titles
    }
    return common_tags.pop() if len(common_tags) == 1 else None


def build_tweet(section: Section) -> str:
    from collectors.twitter.albums import album_emoji

    rows, album = section.rows, section.album
    emoji = album_emoji(album, fallback="") if album else ""
    prefix = "\U0001f3a5 | " + (f"{emoji} " if emoji else "")
    audio = section.group == GROUP_AUDIO
    if len(rows) == 1:
        row = rows[0]
        # Main-channel video: its full title already says what it is.
        title = row.uploads[0]["title"] if len(row.uploads) == 1 and not audio else row.title
        text = f'{prefix}"{title}" debuts with {row.views:,} views in its first 24 hours on YouTube'
        notes = (["official audio"] if audio else []) + (
            [f"{len(row.uploads)} uploads combined"] if len(row.uploads) > 1 else []
        )
        return text + (f" ({', '.join(notes)})" if notes else "") + "."

    suffix = " (official audio)" if audio else ""
    subject = f'"{album}" {section.what}' if album else f"Taylor Swift's {section.what}"
    head = f"{subject} in their first 24 hours on YouTube{suffix}:"
    lines = [f"{row.title} — {row.views:,}" for row in rows]
    budget = int(os.getenv("TWITTER_TEXT_LIMIT", "500")) - 20
    kept = list(lines)
    while True:
        more = len(lines) - len(kept)
        body = "\n".join(kept + ([f"+{more} more"] if more else []))
        text = f"{prefix}{head}\n\n{body}"
        if len(text) <= budget or len(kept) <= 1:
            return text
        kept.pop()


def build_plan(release: Release, captures: dict[str, dict], states: dict[str, str], ctx: _Context) -> Plan:
    rows: dict[tuple[str, str], DebutRow] = {}
    notes: dict[str, str] = {}
    prior_cache: dict[str, bool] = {}
    for member in release.members:
        state = states.get(member.video_id)
        if state != "captured":
            notes[member.video_id] = state or "missing"
            continue
        matched = _match(member.video_id, member.title, ctx)
        is_song = matched.get("matched_catalog") == "1" or member.channel == "topic"
        key = matched["title_key"] if is_song else f"video:{member.video_id}"
        if member.channel == "topic":
            if key not in prior_cache:
                prior_cache[key] = _has_prior_upload(key, matched.get("title") or member.title, release, ctx)
            if prior_cache[key]:
                notes[member.video_id] = "reupload"
                continue
        group = GROUP_AUDIO if member.channel == "topic" else GROUP_VIDEO
        if (group, key) not in rows:
            title = matched["title"] if matched.get("matched_catalog") == "1" else member.title
            rows[(group, key)] = DebutRow(key=key, title=title, is_song=is_song, group=group)
        rows[(group, key)].uploads.append({
            "video_id": member.video_id,
            "title": member.title,
            "channel": member.channel,
            "kind": upload_kind(member.channel, member.title),
            "views": int(captures[member.video_id]["views"]),
            "thumbnail_url": member.thumbnail_url,
            "published_at": _iso(member.published_at),
        })
        notes[member.video_id] = "included"
    sections: list[Section] = []
    for group in GROUP_ORDER:
        group_rows = sorted(
            (row for (row_group, _), row in rows.items() if row_group == group),
            key=lambda row: (-row.views, row.title),
        )
        if group_rows:
            section = Section(group=group, rows=group_rows, album=_release_album(group_rows, release, ctx))
            section.tweet = build_tweet(section)
            sections.append(section)
    return Plan(release=release, sections=sections, notes=notes)


def _render_section(section: Section, release: Release, out_dir: Path, *, keep_html: bool) -> Path:
    from collectors.comp.tables_image import render_html_to_png
    from collectors.comp.youtube_card import (
        render_youtube_card,
        render_youtube_debut_table,
        slugify,
        write_song_card_png,
    )

    audio = section.group == GROUP_AUDIO
    if len(section.rows) == 1:
        row = section.rows[0]
        single = len(row.uploads) == 1
        title = row.uploads[0]["title"] if single and not audio else row.title
        primary = row.primary
        label = "First 24 Hours" + (" · Official Audio" if audio else "")
        if not single:
            label += f" · {len(row.uploads)} uploads"
        html_text = render_youtube_card(
            title=title,
            stat_label=label,
            stat_value=f"+{row.views:,} views",
            cover_url=primary.get("thumbnail_url") or "",
            footer_left=HANDLE,
            badge_text="NEW SONG" if audio else "NEW VIDEO",
            release_date_text=_release_time_text(parse_utc(primary["published_at"]) or release.start),
        )
        base = f"first_day_{slugify(title)}_{primary['video_id']}"
        return write_song_card_png(html_text, out_dir / f"{base}.png", out_dir / f"{base}.html", keep_html=keep_html)

    what = section.what + (" (official audio)" if audio else "")
    start = section.start
    hour = start.strftime("%I").lstrip("0") or "12"
    subtitle = (
        f"First 24 hours on YouTube · {what[0].upper()}{what[1:]}"
        f"\nReleased {start.strftime('%b')} {start.day}, {start.year} · {hour}:{start.strftime('%M %p')} UTC"
    )
    html_text = render_youtube_debut_table(
        title=section.album or "Taylor Swift",
        subtitle=subtitle,
        entity_label="Song" if all(row.is_song for row in section.rows) else "Title",
        rows=[
            {
                "title": row.title,
                "subtitle": row.subtitle,
                "value": f"{row.views:,}",
                "thumbnail_url": row.primary.get("thumbnail_url") or "",
            }
            for row in section.rows
        ],
        date_str=f"Views at +24h · {release.last_due.strftime('%b')} {release.last_due.day}, {release.last_due.year}",
        handle=HANDLE,
    )
    base = f"first_day_release_{release.anchor}_{section.group}"
    return render_html_to_png(
        html_text, out_dir / f"{base}.png", out_dir / f"{base}.html", width=900, keep_html=keep_html,
    )


def render_plan(plan: Plan, out_root: Path = SNAPSHOTS_DIR, *, keep_html: bool = False) -> list[tuple[str, Path]]:
    """One (tweet, image) per section, in thread order."""
    day = plan.release.start.date().isoformat()
    out_dir = out_root / day[:4] / day[5:7] / day
    return [
        (section.tweet, _render_section(section, plan.release, out_dir, keep_html=keep_html))
        for section in plan.sections
    ]


def _repo_path(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)).replace("\\", "/") if path.is_relative_to(REPO_ROOT) else str(path)


def _resolve(release: Release, status: str, now: datetime, *, plan: Plan | None = None,
             posts: list[tuple[str, Path]] | None = None, reason: str = "",
             captures: dict[str, dict] | None = None) -> None:
    captures = captures if captures is not None else load_captures()
    notes = plan.notes if plan else {}
    images = [image for _, image in (posts or [])]
    record = {
        "anchor": release.anchor,
        "status": status,
        "reason": reason,
        "resolved_at": _iso(now),
        "posts": [
            {
                "group": section.group,
                "album": section.album,
                "tweet": section.tweet,
                "image": _repo_path(images[index]) if index < len(images) else "",
                "rows": [
                    {"title": row.title, "views": row.views, "uploads": [u["video_id"] for u in row.uploads]}
                    for row in section.rows
                ],
            }
            for index, section in enumerate(plan.sections if plan else [])
        ],
        "members": [
            {
                **member.to_json(),
                "capture": captures.get(member.video_id),
                "result": notes.get(member.video_id, status),
            }
            for member in release.members
        ],
    }
    _write_json(RELEASES_DIR / f"{release.anchor}.json", record)
    POSTED_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    for member in release.members:
        result = notes.get(member.video_id, status)
        (POSTED_LOCK_DIR / f"{member.video_id}.lock").write_text(
            f"{status} release {release.anchor} {now.date().isoformat()} ({result})\n", encoding="utf-8",
        )
        for path in (PENDING_DIR / f"{member.video_id}.json", CAPTURES_DIR / f"{member.video_id}.json"):
            path.unlink(missing_ok=True)
    # Post task: named after the anchor, which may have been another member
    # while the release was still growing.
    unregister_tasks(
        [capture_task_name(m.video_id) for m in release.members]
        + [post_task_name(m.video_id) for m in release.members]
    )


def _acquire_posting_lock(anchor: str) -> Path | None:
    path = RELEASES_DIR / f"{anchor}.posting"
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists() and time.time() - path.stat().st_mtime > 3600:
        path.unlink(missing_ok=True)  # stale (crashed poster)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"pid {os.getpid()} {_iso(datetime.now(timezone.utc))}\n")
    return path


def _default_poster(posts: list[tuple[str, Path]]) -> bool:
    """1 post -> single tweet; audio + video -> one native 2-post thread."""
    sys.path.insert(0, str(REPO_ROOT / "collectors" / "spotify"))
    from core.twitter import post_image_thread, post_with_image

    if not TWITTER_SESSION.exists():
        print(f"[first_day] ERROR: session Twitter introuvable: {TWITTER_SESSION}")
        return False
    if len(posts) == 1:
        return bool(post_with_image(posts[0][0], posts[0][1], TWITTER_SESSION))
    return bool(post_image_thread(posts, TWITTER_SESSION))


def run_tick(
    now: datetime,
    *,
    poster: Callable[[list[tuple[str, Path]]], bool] | None = None,
    out_root: Path = SNAPSHOTS_DIR,
    log: Callable[[str], None] = print,
) -> list[dict]:
    """Posts every release that is ready, resolves the ones that can no
    longer produce an exact post. Idempotent; safe to call from the daily
    run, the post task and by hand (--first-day-post)."""
    poster = poster or _default_poster
    pending = load_pending()
    if not pending:
        return []
    captures = load_captures()
    ctx: _Context | None = None
    results: list[dict] = []
    for release in group_releases(pending):
        states = release.states(captures, now)
        if "waiting" in states.values() or now < release.post_at:
            continue
        if "captured" not in states.values():
            _resolve(release, "skipped", now, reason="no exact 24h capture", captures=captures)
            log(f"[first_day] release {release.anchor}: aucune capture exacte à +24h — rien posté.")
            results.append({"anchor": release.anchor, "status": "skipped"})
            continue
        if now - release.last_due > POST_MAX_DELAY:
            _resolve(release, "skipped", now, reason="too late to post", captures=captures)
            log(f"[first_day] release {release.anchor}: trop tard pour poster (> {POST_MAX_DELAY}).")
            results.append({"anchor": release.anchor, "status": "skipped"})
            continue
        ctx = ctx or _load_context(pending)
        plan = build_plan(release, captures, states, ctx)
        if not plan.rows:
            _resolve(release, "skipped", now, plan=plan, reason="only re-uploads / nothing exact", captures=captures)
            log(f"[first_day] release {release.anchor}: seulement des ré-uploads Topic — rien posté.")
            results.append({"anchor": release.anchor, "status": "skipped"})
            continue
        lock = _acquire_posting_lock(release.anchor)
        if lock is None:
            log(f"[first_day] release {release.anchor}: post déjà en cours ailleurs.")
            continue
        try:
            posts = render_plan(plan, out_root)
            for index, (tweet, image) in enumerate(posts, 1):
                log(f"[first_day] Post {index}/{len(posts)}:\n{tweet}")
                log(f"[first_day] Image: {image}")
            if poster(posts):
                _resolve(release, "posted", now, plan=plan, posts=posts, captures=captures)
                results.append({"anchor": release.anchor, "status": "posted", "images": [str(i) for _, i in posts]})
            else:
                log(f"[first_day] release {release.anchor}: échec du post — nouvel essai au prochain tick.")
                results.append({"anchor": release.anchor, "status": "failed"})
        finally:
            lock.unlink(missing_ok=True)
    return results


def cancel_pending(now: datetime, *, log: Callable[[str], None] = print) -> int:
    """--first-day-cancel: never post anything for the videos currently
    pending (deleting their Scheduled Tasks by hand is NOT enough — the daily
    run would still capture and post them)."""
    pending = load_pending()
    captures = load_captures()
    for release in group_releases(pending):
        _resolve(release, "cancelled", now, reason="cancelled by hand", captures=captures)
        log(f"[first_day] release {release.anchor} annulée ({len(release.members)} vidéo(s)).")
    return len(pending)


def status_lines(now: datetime) -> list[str]:
    pending = load_pending()
    captures = load_captures()
    if not pending:
        return ["[first_day] Aucune vidéo en attente."]
    lines: list[str] = []
    for release in group_releases(pending):
        states = release.states(captures, now)
        lines.append(
            f"Release {release.anchor} — {len(release.members)} vidéo(s), publiée(s) à partir de "
            f"{_iso(release.start)}, post prévu {_iso(release.post_at)}"
        )
        for member in release.members:
            capture = captures.get(member.video_id)
            detail = f"{int(capture['views']):,} vues ({capture['source']})" if capture else f"due {_iso(member.due)}"
            lines.append(f"  [{states[member.video_id]:8}] {member.video_id} {member.channel:5} {member.title[:60]} — {detail}")
    return lines


def preview(
    now: datetime,
    fetch_stats: Callable[[list[str]], dict[str, dict]],
    out_root: Path,
    *,
    log: Callable[[str], None] = print,
) -> list[Path]:
    """--preview: render what each pending release would post. Members not
    captured yet use a LIVE view count (layout/copy check only, not the real
    24h figure). Writes nothing to the registry, posts nothing."""
    pending = load_pending()
    if not pending:
        log("[preview] Aucune vidéo en attente d'un post first-day.")
        return []
    captures = dict(load_captures())
    missing = [vid for vid in pending if vid not in captures]
    live = fetch_stats(missing) if missing else {}
    for vid in missing:
        if vid in live:
            captures[vid] = {"views": int(live[vid].get("viewCount", 0)), "source": "preview_live"}
    ctx = _load_context(pending)
    images: list[Path] = []
    for release in group_releases(pending):
        states = {m.video_id: "captured" if m.video_id in captures else "missing" for m in release.members}
        plan = build_plan(release, captures, states, ctx)
        if not plan.rows:
            log(f"[preview] release {release.anchor}: rien à poster (ré-uploads uniquement).")
            continue
        posts = render_plan(plan, out_root, keep_html=True)
        live_count = sum(1 for m in release.members if captures.get(m.video_id, {}).get("source") == "preview_live")
        log(f"[preview] release {release.anchor} ({live_count} valeur(s) live, pas encore à +24h)")
        for index, (tweet, image) in enumerate(posts, 1):
            log(f"[preview] Post {index}/{len(posts)}:\n{tweet}")
            log(f"[preview] Image: {image}")
            images.append(image)
    return images


# ---------------------------------------------------------------------------
# Windows Scheduled Tasks
# ---------------------------------------------------------------------------

def capture_task_name(video_id: str) -> str:
    # Historical name (tasks from before 2026-09-27 used it for the post).
    return f"TSM_YouTube_FirstDay_{video_id}"


def post_task_name(anchor: str) -> str:
    return f"TSM_YouTube_FirstDayPost_{anchor}"


def unregister_tasks(task_names: list[str]) -> None:
    """One PowerShell call for the whole list; missing tasks are ignored."""
    if not task_names:
        return
    names = ",".join(f"'{name}'" for name in task_names)
    try:
        subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"foreach ($n in @({names})) {{ Unregister-ScheduledTask -TaskName $n "
                "-Confirm:$false -ErrorAction SilentlyContinue }",
            ],
            capture_output=True, text=True, timeout=120,
        )
    except Exception:
        pass


def unregister_task(task_name: str) -> None:
    unregister_tasks([task_name])


def _register_one_off_task(task_name: str, when_utc: datetime, arguments: str, *, log: Callable[[str], None] = print) -> bool:
    at_str = when_utc.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    ps_script = (
        f"$action = New-ScheduledTaskAction -Execute '{sys.executable}' "
        f"-Argument '{arguments}' -WorkingDirectory '{REPO_ROOT}'; "
        f"$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date -Date '{at_str}'); "
        f"$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd "
        f"-ExecutionTimeLimit (New-TimeSpan -Minutes 20); "
        f"Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger "
        f"-Settings $settings -Force | Out-Null"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            check=True, capture_output=True, text=True, timeout=30,
        )
        log(f"[first_day] tâche {task_name} planifiée pour {at_str} (heure locale).")
        return True
    except Exception as exc:
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else exc
        log(f"[first_day] ERROR planification {task_name}: {detail}")
        return False


def schedule_capture_task(candidate: Candidate, now: datetime, *, log: Callable[[str], None] = print) -> bool:
    if candidate.due <= now + timedelta(minutes=2):
        return False  # 24h mark already reached: only the daily-run capture can apply
    return _register_one_off_task(
        capture_task_name(candidate.video_id),
        max(candidate.due - CAPTURE_LEAD, now + timedelta(minutes=1)),
        f"-m collectors.youtube.update_youtube --capture-first-day {candidate.video_id}",
        log=log,
    )


def schedule_post_tasks(now: datetime, *, log: Callable[[str], None] = print) -> None:
    """(Re)plans one post tick per unresolved release at last 24h mark +
    POST_GRACE. Re-registering is idempotent (-Force) and follows a release
    that grew since the last run."""
    captures = load_captures()
    for release in group_releases(load_pending()):
        if "waiting" not in release.states(captures, now).values() and release.post_at <= now:
            continue  # already ready: the daily run's own tick handles it
        when = max(release.post_at, now + timedelta(minutes=1))
        if when - release.last_due > POST_MAX_DELAY:
            continue
        _register_one_off_task(
            post_task_name(release.anchor), when,
            "-m collectors.youtube.update_youtube --first-day-post", log=log,
        )


def handle_daily_run(
    new_rows: list[dict],
    all_rows: list[dict],
    snapshot_at: datetime,
    now: datetime,
    *,
    log: Callable[[str], None] = print,
) -> None:
    """Daily collection hook, right after the CSV is written: register the
    videos discovered now, capture every pending video whose 24h mark is this
    snapshot, plan the exact-24h capture tasks and the release post ticks."""
    for candidate in register_candidates(new_rows, now):
        schedule_capture_task(candidate, now, log=log)
    captured = capture_from_daily_rows(all_rows, snapshot_at)
    if captured:
        log(f"[first_day] {len(captured)} capture(s) +24h prise(s) sur ce snapshot : {', '.join(captured)}")
    schedule_post_tasks(now, log=log)
