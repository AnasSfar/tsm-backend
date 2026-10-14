"""Theme of the day for the Spotify streams run (image + colour), from the Taylor Swift timeline.

Owner rules (2026-10-13):
- Every event of `taylor_swift_timeline_en_2006_2026.json` (repo root) gives an image + a theme.
- Anniversary = the event's month-day matches the POST date OR the STATS date (post = stats + 1).
  The post-date match wins; several events on one day -> the most important type, then the
  most recent year. Events from less than a year ago are not anniversaries.
- On an event day the theme applies to every daily streams image except the album cards (they
  keep their own album colour); the recap (artist page) also writes the event under the name.
- Day without event: one of 3 modes, picked at random but seeded by the post date (re-runs give
  the same result): era of the most recent past event, daily portrait rotation, era #1 of the day.

Image = the Eras Tour portrait of the event's era (assets/eras-tour-hero/panel-01..12, album
order); Eras Tour events (tour, concert films, documentary) use the 6x2 collage of all eras.
Colour = owner overrides for monochrome eras (album_page_card.HEADER_OVERRIDES), else taken from
the portrait itself. Read-only: no state, nothing written.
"""
from __future__ import annotations

import colorsys
import json
import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
TIMELINE_PATH = REPO / "taylor_swift_timeline_en_2006_2026.json"
# Built by scripts/build_timeline_assets.py: same events + real photos (posters, covers,
# concert / ceremony photos) in 3 sizes. Missing -> era portraits only.
TIMELINE_ASSETS = REPO / "assets" / "timeline"
HERO_DIR = REPO / "assets" / "eras-tour-hero"
ERAS_TOUR_IMAGE = HERO_DIR / "collage-6x2.jpg"

# (era key, display name, title patterns, portrait) in album order. TV re-recordings and the
# Encore belong to their original era. Debut is only matched on "album Taylor Swift" so that
# "Taylor Swift: The Eras Tour" is not read as the debut.
ERAS = [
    ("taylor swift", "Taylor Swift", [r"\balbum Taylor Swift\b"], "panel-01.jpg"),
    ("fearless", "Fearless", [r"\bFearless\b", r"\bLove Story\b"], "panel-02.jpg"),
    ("speak now", "Speak Now", [r"\bSpeak Now\b"], "panel-03.jpg"),
    ("red", "Red", [r"\bRed\b"], "panel-04.jpg"),
    ("1989", "1989", [r"\b1989\b", r"\bShake It Off\b"], "panel-05.jpg"),
    ("reputation", "reputation", [r"\breputation\b"], "panel-06.jpg"),
    ("lover", "Lover", [r"\bLover\b"], "panel-07.jpg"),
    ("folklore", "folklore", [r"\bfolklore\b"], "panel-08.jpg"),
    ("evermore", "evermore", [r"\bevermore\b"], "panel-09.jpg"),
    ("midnights", "Midnights", [r"\bMidnights\b"], "panel-10.jpg"),
    ("the tortured poets department", "THE TORTURED POETS DEPARTMENT", [r"\bTortured Poets\b"], "panel-11.jpg"),
    ("the life of a showgirl", "The Life of a Showgirl", [r"\bShowgirl\b", r"\bOpalite\b", r"\bPatient Zero\b"], "panel-12.jpg"),
]
ERAS_TOUR_RE = re.compile(r"\bEras Tour\b|\bEnd of an Era\b", re.I)

# Most important first (several events on the same day).
TYPE_PRIORITY = ["album_release", "award", "film_release", "documentary_release", "chart_milestone",
                 "tour_start", "tour_end", "single_release", "music_video_release", "album_announcement",
                 "tour_announcement", "film_announcement", "career_milestone", "personal_milestone",
                 "award_announcement", "masters_dispute", "culture_event"]
NO_EVENT_MODES = ("latest_event", "rotation", "top_era")


@dataclass
class DayTheme:
    mode: str                      # "event" | "latest_event" | "rotation" | "top_era"
    image: Path
    color: str
    era: str | None = None         # era key (ERAS[i][0]) or "eras tour"
    caption: str | None = None     # recap line, event days only
    image_kind: str = "portrait"   # timeline kind (poster, album_cover, photo...) or "portrait" (era portrait)
    event: dict | None = field(default=None)

    @property
    def applies_to_all(self) -> bool:
        """Event day: every non-album streams image takes this colour."""
        return self.mode == "event"


def load_timeline(path: Path = TIMELINE_PATH) -> list[dict]:
    try:
        events = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"[day_theme] timeline unavailable ({exc})")
        return []
    return [e for e in events if isinstance(e, dict) and re.match(r"\d{4}-\d{2}-\d{2}$", str(e.get("date") or ""))]


def _clean(text: str) -> str:
    return (text or "").replace("�", "'").replace("’", "'")


def event_era(event: dict, events: list[dict]) -> str:
    """Era named in the title (longest/most specific wins), Eras Tour events apart, else the
    era of the latest album released OR announced on or before the event (the engagement in
    Aug. 2025 is Showgirl era, the 2019 masters sale is Lover era)."""
    title = _clean(event.get("title") or "")
    if ERAS_TOUR_RE.search(title):
        return "eras tour"
    for key, _name, patterns, _img in reversed(ERAS):  # later eras first: "Red (Taylor's Version)" etc. stay Red
        if any(re.search(p, title, re.I) for p in patterns):
            return key
    released = sorted((e for e in events if e.get("type") in ("album_release", "album_announcement")
                       and e["date"] <= event["date"]),
                      key=lambda e: e["date"])
    for e in reversed(released):
        era = event_era(e, [])
        if era and era != "eras tour":
            return era
    return ERAS[0][0]


def era_image(era: str | None) -> Path:
    if era == "eras tour":
        return ERAS_TOUR_IMAGE
    for key, _n, _p, img in ERAS:
        if key == era:
            return HERO_DIR / img
    return HERO_DIR / ERAS[0][3]


def photo_tone(path: Path) -> str:
    """Average of the photo's bottom third, kept mid-dark and not too saturated (white text)."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    r, g, b = im.crop((0, im.height * 2 // 3, im.width, im.height)).resize((1, 1), Image.LANCZOS).getpixel((0, 0))
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    r, g, b = colorsys.hsv_to_rgb(h, min(s, 0.6), min(max(v, 0.28), 0.42))
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def era_color(era: str | None) -> str:
    """Same colour as that era's album card: owner overrides, else the album cover's tone
    (album_page_card.header_color). Eras Tour / no cover -> the photo's own tone."""
    try:
        from album_page_card import HEADER_OVERRIDES, header_color
        from comp.discography import build_cover_map
        from comp.milestone_card import _cover, milestone_palette
        if era in HEADER_OVERRIDES:
            return HEADER_OVERRIDES[era]
        covers = build_cover_map(REPO / "db" / "discography" / "covers.json")
        key = re.sub(r"[^a-z0-9]+", "_", era or "").strip("_")
        # colour from the Taylor's Version cover when there is one (1989 = blue); Top Eras shows
        # an OG | TV split cover for these eras, but the colour stays the TV one
        url = covers.get(f"{key}_taylor_s_version") or covers.get(key)
        cover_bytes = _cover(url)[1] if url else b""
        if cover_bytes:
            return header_color(milestone_palette(cover_bytes))
    except Exception as exc:
        print(f"[day_theme] era colour from cover unavailable ({exc})")
    return photo_tone(era_image(era))


def image_color(path: Path, era: str | None) -> str:
    """Event day: the background follows the accent of the picture actually shown (Eras Tour
    poster = pink, owner 2026-10-13), not a fixed era colour. Monochrome eras keep the owner
    override (folklore grey, reputation black...)."""
    try:
        from album_page_card import HEADER_OVERRIDES, header_color
        from comp.milestone_card import milestone_palette
        if era in HEADER_OVERRIDES:
            return HEADER_OVERRIDES[era]
        return header_color(milestone_palette(path.read_bytes()))
    except Exception as exc:
        print(f"[day_theme] image colour unavailable ({exc})")
        return era_color(era)


def era_from_album(album: str | None) -> str | None:
    """Top Eras album name -> era key ("Red (Taylor's Version)" -> "red")."""
    for key, _n, patterns, _img in reversed(ERAS):
        if any(re.search(p, album or "", re.I) for p in patterns) or (album or "").strip().casefold() == key:
            return key
    return None


def _anniversaries(events: list[dict], day: date) -> list[dict]:
    md = day.strftime("%m-%d")
    out = [e for e in events if e["date"][5:] == md and int(e["date"][:4]) < day.year]
    rank = {t: i for i, t in enumerate(TYPE_PRIORITY)}
    return sorted(out, key=lambda e: (rank.get(e.get("type"), len(rank)), -int(e["date"][:4])))


def _latest_past_event(events: list[dict], day: date) -> dict | None:
    """Most recent event by calendar day (month-day), looking back across the year boundary."""
    md = day.strftime("%m-%d")
    past = [e for e in events if e["date"][5:] < md] or events
    return max(past, key=lambda e: (e["date"][5:], e["date"]), default=None)


# Which picture fits which kind of event, best first (owner 2026-10-13: "use the right images",
# e.g. the film poster for a film). Kinds come from build_timeline_assets.py.
IMAGE_PREFERENCE = {
    "film_release": ["poster", "page_main", "photo"],
    "film_announcement": ["poster", "page_main", "photo"],
    "documentary_release": ["poster", "page_main", "photo"],
    "album_release": ["album_cover", "page_main", "photo"],
    "album_announcement": ["album_cover", "page_main", "single_cover", "photo"],
    "single_release": ["single_cover", "page_main", "photo"],
    "chart_milestone": ["single_cover", "page_main", "photo"],
    "music_video_release": ["single_cover", "page_main", "photo"],
    "tour_start": ["photo", "poster", "page_main"],
    "tour_end": ["photo", "page_main"],
    "tour_announcement": ["poster", "page_main", "photo"],
}
DEFAULT_PREFERENCE = ["photo", "page_main", "poster", "album_cover", "single_cover"]


def _assets_index() -> dict[tuple[str, str], dict]:
    try:
        data = json.loads((TIMELINE_ASSETS / "timeline.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {(e["date"], _clean(e["title"]).casefold()): e for e in data if isinstance(e, dict)}


def event_image(ev: dict, size: int = 1600) -> Path | None:
    info = event_image_info(ev, size)
    return info[0] if info else None


def event_image_info(ev: dict, size: int = 1600) -> tuple[Path, str] | None:
    """Best real picture of the event for its type (portrait photos first among photos, they
    fill the recap's portrait column), or None when the asset folder has nothing for it."""
    entry = _assets_index().get((ev["date"], _clean(ev.get("title") or "").casefold()))
    if not entry or not entry.get("images"):
        return None
    prefs = IMAGE_PREFERENCE.get(ev.get("type"), DEFAULT_PREFERENCE)

    def score(im: dict) -> tuple:
        kind = im.get("kind")
        rank = prefs.index(kind) if kind in prefs else len(prefs)
        return (rank, {"portrait": 0, "square": 1, "landscape": 2}.get(im.get("orientation"), 3))

    for im in sorted(entry["images"], key=score):
        f = (im.get("sizes") or {}).get(str(size), {}).get("file")
        if f and (TIMELINE_ASSETS / f).exists():
            return TIMELINE_ASSETS / f, im.get("kind") or "photo"
    return None


def theme_for(stats_date: str, *, top_era_album: str | None = None, events: list[dict] | None = None) -> DayTheme:
    events = load_timeline() if events is None else events
    stats_day = date.fromisoformat(stats_date)
    post_day = stats_day + timedelta(days=1)
    for day, is_post in ((post_day, True), (stats_day, False)):
        hits = _anniversaries(events, day)
        if hits:
            ev = hits[0]
            era = event_era(ev, events)
            years = day.year - int(ev["date"][:4])
            when = "today" if is_post else f"on {day.strftime('%B')} {day.day}"
            caption = f"{years} year{'s' if years > 1 else ''} ago {when} · {_clean(ev['title'])}"
            img, kind = event_image_info(ev) or (era_image(era), "portrait")
            return DayTheme("event", img, image_color(img, era), era, caption, event=ev, image_kind=kind)

    mode = random.Random(f"tsm-day-theme:{post_day.isoformat()}").choice(NO_EVENT_MODES)
    if mode == "top_era" and era_from_album(top_era_album):
        era = era_from_album(top_era_album)
        return DayTheme(mode, era_image(era), era_color(era), era)
    if mode == "latest_event":
        ev = _latest_past_event(events, post_day)
        if ev:
            era = event_era(ev, events)
            return DayTheme(mode, era_image(era), era_color(era), era, event=ev)
    panels = sorted(HERO_DIR.glob("panel-[0-9][0-9].jpg"))
    img = panels[post_day.toordinal() % len(panels)]
    return DayTheme("rotation", img, photo_tone(img))


if __name__ == "__main__":
    import sys
    for d in sys.argv[1:] or [str(date.today() - timedelta(days=1))]:
        t = theme_for(d)
        print(d, t.mode, t.era, t.image.name, t.color, t.caption or "")
