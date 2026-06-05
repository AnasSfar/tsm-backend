#!/usr/bin/env python3
"""Generate and post a priority Global Spotify Charts card for NEW songs."""
from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

try:
    from PIL import Image as PilImage
    _PIL = True
except ImportError:
    _PIL = False

ROOT = Path(__file__).resolve().parents[6]
SPOTIFY_ROOT = ROOT / "collectors" / "spotify"
if str(SPOTIFY_ROOT) not in sys.path:
    sys.path.insert(0, str(SPOTIFY_ROOT))

from core.data_paths import LEGACY_WEBSITE_DATA_DIR, WEB_EXPORT_DATA_DIR, first_existing, spotify_chart_dir  # noqa: E402
from core.twitter import post_with_image  # noqa: E402

TWITTER_SESSION = ROOT / "collectors" / "spotify" / "charts" / "worldwide" / "tools" / "json" / "twitter_session.json"
DISCOGRAPHY_DIR = ROOT / "db" / "discography"
SONGS_JSON = first_existing(WEB_EXPORT_DATA_DIR / "songs.json", LEGACY_WEBSITE_DATA_DIR / "songs.json")
HANDLE = "@tsmuseum13"
PRIORITY_WINDOW_DAYS = 7


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _norm(value)).strip("_") or "song"


def _fmt(value) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return "-"


def _fmt_change(value, *, invert: bool = False) -> tuple[str, str]:
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return ("", "")
    if amount == 0:
        return ("0", "flat")
    positive = amount > 0
    color = "up" if positive else "down"
    signed = f"+{_fmt(abs(amount))}" if positive else f"-{_fmt(abs(amount))}"
    if invert:
        color = "down" if positive else "up"
    return (signed, color)


def _fmt_pct(value) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return ""
    sign = "+" if amount > 0 else ""
    return f"{sign}{amount:.1f}%"


def _track_id_from_url(value: str | None) -> str:
    match = re.search(r"track/([A-Za-z0-9]+)", value or "")
    return match.group(1) if match else ""


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _parse_date(value: str | None):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _iter_discography_tracks():
    albums_dir = DISCOGRAPHY_DIR / "albums"
    if albums_dir.exists():
        for path in sorted(albums_dir.glob("*.json")):
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            album = payload.get("album") or ""
            for section in payload.get("sections") or []:
                for track in section.get("tracks") or []:
                    if isinstance(track, dict):
                        yield {**track, "album": track.get("album") or album}

    for path in (DISCOGRAPHY_DIR / "songs.json", DISCOGRAPHY_DIR / "features.json", DISCOGRAPHY_DIR / "misc.json"):
        payload = _read_json(path)
        sections = payload if isinstance(payload, list) else []
        for section in sections:
            album = section.get("album") or section.get("section") or ""
            for track in section.get("tracks") or []:
                if isinstance(track, dict):
                    yield {**track, "album": track.get("album") or album}


def _load_song_meta() -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    by_title: dict[str, dict] = {}

    def remember(target: dict[str, dict], key: str, item: dict) -> None:
        if not key:
            return
        existing = target.setdefault(key, item)
        for field in ("track_id", "title", "album", "image_url", "release_date"):
            if not existing.get(field) and item.get(field):
                existing[field] = item[field]

    payload = _read_json(SONGS_JSON)
    for song in (payload.get("songs") if isinstance(payload, dict) else []) or []:
        tid = str(song.get("track_id") or "").strip()
        title = str(song.get("title") or song.get("name") or "").strip()
        item = {
            "track_id": tid,
            "title": title,
            "album": song.get("primary_album") or song.get("album") or "",
            "image_url": song.get("image_url") or song.get("apple_music_image_url") or "",
            "release_date": song.get("release_date") or song.get("released_at") or "",
        }
        remember(by_id, tid, item)
        remember(by_title, _norm(title), item)

    for track in _iter_discography_tracks():
        tid = _track_id_from_url(track.get("url") or track.get("spotify_url")) or str(track.get("track_id") or "")
        title = str(track.get("title") or "").strip()
        item = {
            "track_id": tid,
            "title": title,
            "album": track.get("album") or "",
            "image_url": track.get("image_url") or "",
            "release_date": track.get("release_date") or track.get("released_at") or "",
        }
        remember(by_id, tid, item)
        remember(by_title, _norm(title), item)

    return {"by_id": by_id, "by_title": by_title}


def _chart_path(chart_date: str) -> Path:
    return spotify_chart_dir("global", chart_date) / f"ts_chart_{chart_date}.json"


def _previous_chart_date(chart_date: str) -> str | None:
    day = _parse_date(chart_date)
    if day is None:
        return None
    return (day - timedelta(days=1)).isoformat()


def _selector_matches(row: dict, selector: str) -> bool:
    needle = (selector or "").strip()
    if not needle:
        return False
    folded = needle.casefold()
    return folded in {
        str(row.get("track_id") or "").casefold(),
        str(row.get("track_name") or row.get("song_name") or "").casefold(),
    }


def _same_track(left: dict, right: dict) -> bool:
    left_id = str(left.get("track_id") or "").casefold()
    right_id = str(right.get("track_id") or "").casefold()
    if left_id and right_id and left_id == right_id:
        return True
    return _norm(str(left.get("title") or left.get("track_name") or left.get("song_name") or "")) == _norm(
        str(right.get("title") or right.get("track_name") or right.get("song_name") or "")
    )


def _is_api_new_row(row: dict) -> bool:
    if row.get("is_new") is True:
        return True
    if "is_new" in row:
        return False

    # Backward-compatible fallback for old snapshots. New runs set is_new
    # directly from Spotify Charts API fields in worldwide/daily.py.
    previous_rank = row.get("previous_rank")
    total_days = row.get("total_days") or row.get("streak")
    try:
        total_days_int = int(total_days) if total_days not in (None, "") else 1
    except (TypeError, ValueError):
        total_days_int = 1
    return previous_rank in (None, "", 0) and total_days_int <= 1


def _load_chart_rows(chart_date: str) -> list[dict]:
    path = _chart_path(chart_date)
    rows = _read_json(path)
    if not isinstance(rows, list):
        return []
    return rows


def _with_daily_changes(rows: list[dict], chart_date: str) -> list[dict]:
    previous_date = _previous_chart_date(chart_date)
    previous_rows = _enrich_rows(_load_chart_rows(previous_date)) if previous_date else []
    out = []
    for row in rows:
        item = dict(row)
        previous = next((prev for prev in previous_rows if _same_track(item, prev)), None)
        previous_rank = row.get("previous_rank")
        if previous_rank in (None, "") and previous:
            previous_rank = previous.get("rank")
        try:
            if previous_rank not in (None, "") and row.get("rank") not in (None, ""):
                item["rank_change"] = int(previous_rank) - int(row.get("rank"))
        except (TypeError, ValueError):
            pass
        if previous and previous.get("streams") not in (None, "") and row.get("streams") not in (None, ""):
            try:
                item["streams_change"] = int(row.get("streams")) - int(previous.get("streams"))
                item["streams_change_pct"] = item["streams_change"] / int(previous.get("streams")) * 100
            except (TypeError, ValueError):
                pass
        out.append(item)
    return out


def _load_priority_rows(chart_date: str, *, force_songs: set[str] | None = None) -> list[dict]:
    chart_day = _parse_date(chart_date)
    rows = _enrich_rows(_load_chart_rows(chart_date))
    force_songs = force_songs or set()
    priority_rows = []
    for row in rows:
        if any(_selector_matches(row, selector) for selector in force_songs):
            forced = dict(row)
            forced["is_new"] = True
            forced["priority_reason"] = "forced"
            priority_rows.append(forced)
            continue
        if _is_api_new_row(row):
            item = dict(row)
            item["priority_reason"] = "api_new"
            priority_rows.append(item)
            continue

        release_day = _parse_date(row.get("release_date"))
        if chart_day and release_day:
            age_days = (chart_day - release_day).days
            if 0 <= age_days < PRIORITY_WINDOW_DAYS:
                item = dict(row)
                item["release_age_days"] = age_days
                item["priority_reason"] = "release_window"
                priority_rows.append(item)
    return _with_daily_changes(sorted(priority_rows, key=lambda item: int(item.get("rank") or 9999)), chart_date)


def _image_data_uri(url: str | None) -> tuple[str, bytes]:
    if not url:
        return "", b""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=8) as response:
            data = response.read()
            mime = response.headers.get_content_type() or "image/jpeg"
            return f"data:{mime};base64,{base64.b64encode(data).decode()}", data
    except Exception:
        return "", b""


def _boost_color(rgb: tuple[int, int, int], *, min_sat: float = 0.48, min_val: float = 0.34) -> tuple[str, str]:
    import colorsys

    r, g, b = rgb
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    s = min(1.0, max(min_sat, s * 1.25))
    v = min(0.86, max(min_val, v * 0.86))
    rb, gb, bb = colorsys.hsv_to_rgb(h, s, v)
    color = f"#{int(rb * 255):02x}{int(gb * 255):02x}{int(bb * 255):02x}"
    return color, f"rgba({int(rb * 255)},{int(gb * 255)},{int(bb * 255)},.42)"


def _cover_palette(img_bytes: bytes) -> tuple[str, str]:
    if not _PIL or not img_bytes:
        return ("linear-gradient(135deg,#1db954 0%,#0f7f3d 100%)", "#1db954")
    try:
        from io import BytesIO

        import colorsys

        img = PilImage.open(BytesIO(img_bytes)).convert("RGB").resize((120, 120), PilImage.LANCZOS)
        colors = img.quantize(colors=14, method=PilImage.Quantize.MEDIANCUT).convert("RGB").getcolors(120 * 120) or []
        candidates = []
        for count, color in colors:
            r, g, b = color
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            hue = h * 360
            if (r > 235 and g > 235 and b > 235) or (r < 18 and g < 18 and b < 18):
                continue
            # Olive/brown shadows are common in covers but read muddy as a card background.
            mud_penalty = 0.6 if 35 <= hue <= 78 and s < 0.62 else 0.0
            score = (count ** 0.5) * (0.35 + s) * (0.35 + v) - mud_penalty
            candidates.append({"rgb": color, "hue": hue, "sat": s, "val": v, "score": score})
        if not candidates:
            candidates = [{"rgb": (29, 185, 84), "hue": 141, "sat": 0.84, "val": 0.73, "score": 1}]

        cool = [c for c in candidates if 150 <= c["hue"] <= 220 and c["sat"] >= 0.24]
        warm = [c for c in candidates if (0 <= c["hue"] <= 32 or 330 <= c["hue"] <= 360) and c["sat"] >= 0.32]
        primary_choice = max(cool or candidates, key=lambda c: c["score"] + (0.8 if 165 <= c["hue"] <= 200 else 0))
        secondary_choice = max(warm or candidates, key=lambda c: c["score"] + (0.5 if c in warm else 0))
        primary, _ = _boost_color(primary_choice["rgb"], min_sat=0.58, min_val=0.40)
        secondary, glow = _boost_color(secondary_choice["rgb"], min_sat=0.52, min_val=0.30)
        gradient = (
            f"radial-gradient(circle at 76% 18%,{glow} 0%,rgba(16,24,40,0) 26%),"
            f"linear-gradient(135deg,{primary} 0%,#101828 100%)"
        )
        return (gradient, primary)
    except Exception:
        return ("linear-gradient(135deg,#1db954 0%,#0f7f3d 100%)", "#1db954")


SPOTIFY_SVG = """<svg class="logo" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg"><path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/></svg>"""


CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  background:
    radial-gradient(circle at 12% 18%, rgba(29,185,84,.13), transparent 30%),
    radial-gradient(circle at 84% 16%, rgba(126,87,255,.10), transparent 32%),
    linear-gradient(180deg,#f4f7f8 0%,#edf3f4 100%);
  width:800px;
  color:#101828;
}
.container{overflow:hidden}
.hdr{
  padding:43px 22px;
  display:flex;align-items:center;gap:16px;
  background:linear-gradient(135deg,#1db954 0%,#17a34a 100%);
}
.logo{width:52px;height:52px;flex-shrink:0}
.hdr-title{color:#fff;font-size:22px;font-weight:800;letter-spacing:0}
.hdr-sub{color:rgba(255,255,255,.85);font-size:13px;margin-top:4px}
.col-heads{
  display:grid;
  grid-template-columns:52px 60px minmax(250px,1fr) 132px 74px 74px;
  column-gap:8px;
  padding:7px 14px;
  background:rgba(241,245,246,.95);
  border-bottom:1px solid rgba(16,24,40,.07);
}
.col-heads span{
  font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;color:#667085;
  display:flex;align-items:center;
}
.col-heads .right{justify-content:flex-end}
.song-card{
  display:grid;
  grid-template-columns:52px 60px minmax(250px,1fr) 132px 74px 74px;
  column-gap:8px;
  align-items:center;
  padding:10px 14px;
  background:rgba(255,255,255,.82);
  border-bottom:1px solid rgba(16,24,40,.05);
}
.song-card.row-odd{background:rgba(248,250,251,.88)}
.song-card.row-gold{
  background:linear-gradient(90deg,#fff7d6 0%,#fffdf5 40%,rgba(255,255,255,.92) 100%);
  border-left:3px solid #ebc44c;
}
.col-rank{
  font-size:17px;font-weight:900;color:#0b1f44;
  display:flex;align-items:center;justify-content:center;
}
.col-chg{
  font-size:10px;font-weight:800;
  display:flex;align-items:center;justify-content:center;
  color:#5bbde4;
}
.col-song{display:flex;align-items:center;gap:10px;min-width:0}
.art{
  width:42px;height:42px;border-radius:6px;
  flex-shrink:0;object-fit:cover;
  box-shadow:0 2px 8px rgba(0,0,0,.12);
}
.art-ph{width:42px;height:42px;border-radius:6px;background:#dde3ea;flex-shrink:0}
.song-info{min-width:0}
.song-title{
  font-size:13px;font-weight:700;color:#101828;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.song-artist{font-size:11px;color:#667085;margin-top:2px}
.col-num{
  font-size:12px;color:#344054;font-weight:500;
  display:flex;align-items:center;justify-content:flex-end;
}
.ftr{
  background:rgba(241,245,246,.96);
  padding:8px 16px;
  display:flex;justify-content:space-between;align-items:center;
  border-top:1px solid rgba(16,24,40,.07);
}
.ftr-handle{font-size:11px;color:#1db954;font-weight:700}
.ftr-date{font-size:11px;color:#667085;font-weight:500}
"""


def _enrich_rows(rows: list[dict]) -> list[dict]:
    meta = _load_song_meta()
    out = []
    for row in rows:
        tid = str(row.get("track_id") or "").strip()
        title = str(row.get("track_name") or row.get("song_name") or "").strip()
        item = meta["by_id"].get(tid) if tid else None
        if item is None:
            item = meta["by_title"].get(_norm(title), {})
        out.append({
            **row,
            "track_id": tid or item.get("track_id") or "",
            "title": title or item.get("title") or "New song",
            "album": item.get("album") or "",
            "image_url": row.get("image_url") or item.get("image_url") or "",
            "release_date": row.get("release_date") or item.get("release_date") or "",
        })
    return out


def _build_html(rows: list[dict], chart_date: str) -> tuple[str, str]:
    primary = rows[0]
    date_text = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    count = len(rows)
    title = primary["title"] if count == 1 else f"{count} New Taylor Swift Songs"
    rows_html = []
    for idx, row in enumerate(rows[:8]):
        cover_uri, _cover_bytes = _image_data_uri(row.get("image_url"))
        art_html = f'<img class="art" src="{cover_uri}" />' if cover_uri else '<div class="art-ph"></div>'
        card_cls = "song-card"
        if int(row.get("rank") or 0) == 1:
            card_cls += " row-gold"
        elif idx % 2:
            card_cls += " row-odd"
        rows_html.append(f"""<div class="{card_cls}">
  <div class="col-rank">#{html.escape(str(row.get("rank") or "-"))}</div>
  <div class="col-chg">NEW</div>
  <div class="col-song">
    {art_html}
    <div class="song-info">
      <div class="song-title">{html.escape(str(row.get("title") or "New song"))}</div>
      <div class="song-artist">Taylor Swift</div>
    </div>
  </div>
  <div class="col-num">{_fmt(row.get("streams"))}</div>
  <div class="col-num">NEW</div>
  <div class="col-num">{html.escape(str(row.get("total_days") or row.get("streak") or "-"))}d</div>
</div>""")
    extra_count = max(count - len(rows_html), 0)
    extra_html = ""
    if extra_count:
        extra_html = f"""<div class="song-card row-odd">
  <div class="col-rank"></div>
  <div class="col-chg">NEW</div>
  <div class="col-song"><div class="art-ph"></div><div class="song-info"><div class="song-title">{extra_count} more new song{'s' if extra_count > 1 else ''}</div><div class="song-artist">Taylor Swift</div></div></div>
  <div class="col-num"></div>
  <div class="col-num">NEW</div>
  <div class="col-num"></div>
</div>"""
    html_text = f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>
<div class="container">
  <div class="hdr">
    {SPOTIFY_SVG}
    <div>
      <div class="hdr-title">Taylor Swift · Global Spotify</div>
      <div class="hdr-sub">Daily Chart · {html.escape(date_text)} · {html.escape(title)}</div>
    </div>
  </div>
  <div class="col-heads">
    <span>Pos</span>
    <span>Chg</span>
    <span>Track</span>
    <span class="right">Streams</span>
    <span class="right">Daily</span>
    <span class="right">Total</span>
  </div>
  {''.join(rows_html)}
  {extra_html}
  <div class="ftr">
    <span class="ftr-handle">{HANDLE}</span>
    <span class="ftr-date">{html.escape(date_text)}</span>
  </div>
</div>
</body></html>"""
    return html_text, _slug(title)


def generate_card(chart_date: str, *, force_songs: set[str] | None = None) -> Path | None:
    rows = _load_priority_rows(chart_date, force_songs=force_songs)
    if not rows:
        print(f"[global-new] No priority Global Spotify Chart entries for {chart_date}.")
        return None
    html_text, slug = _build_html(rows, chart_date)
    out_dir = spotify_chart_dir("global", chart_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"global_new_{slug}.png"
    tmp_path = out_dir / "_global_new_tmp.html"
    tmp_path.write_text(html_text, encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 800, "height": 340}, device_scale_factor=2)
            page.goto(f"file:///{tmp_path.as_posix()}", wait_until="load")
            page.locator("body").screenshot(path=str(out_path))
            browser.close()
    finally:
        tmp_path.unlink(missing_ok=True)
    print(f"[global-new] Card generated: {out_path}")
    return out_path


def post_card(
    chart_date: str,
    *,
    force: bool = False,
    no_post: bool = False,
    force_songs: set[str] | None = None,
) -> int:
    rows = _load_priority_rows(chart_date, force_songs=force_songs)
    if not rows:
        print(f"[global-new] No priority post needed for {chart_date}.")
        return 0

    out_dir = spotify_chart_dir("global", chart_date)
    lock_path = out_dir / "global_new_releases_posted.json"
    slugs = sorted(_slug(row.get("track_id") or row["title"]) for row in rows)
    if lock_path.exists() and not force:
        try:
            posted = set(json.loads(lock_path.read_text(encoding="utf-8")).get("posted", []))
        except Exception:
            posted = set()
        if set(slugs).issubset(posted):
            print(f"[global-new] Priority Global card already posted for {chart_date}.")
            return 0

    image_path = generate_card(chart_date, force_songs=force_songs)
    if image_path is None:
        return 0

    date_text = datetime.strptime(chart_date, "%Y-%m-%d").strftime("%B %d, %Y")
    if len(rows) == 1:
        row = rows[0]
        verb = "debuts" if row.get("priority_reason") in {"api_new", "forced"} else "charts"
        tweet = (
            f'"{row["title"]}" {verb} at #{row.get("rank")} on Global Spotify Charts '
            f"with {_fmt(row.get('streams'))} streams ({date_text}).\n\n"
            "See full update here : https://thetsmuseum.app/charts?region=global&view=today"
        )
    else:
        tweet = (
            f"{len(rows)} new Taylor Swift songs debuted on Global Spotify Charts ({date_text}).\n\n"
            "See full update here : https://thetsmuseum.app/charts?region=global&view=today"
        )
    print(f"[global-new] Tweet: {tweet}")
    if no_post:
        print("[global-new] Twitter post skipped (--no-post).")
        return 0
    if not TWITTER_SESSION.exists():
        print(f"[global-new] Twitter session missing: {TWITTER_SESSION}")
        return 1
    if not post_with_image(tweet, image_path, TWITTER_SESSION):
        print("[global-new] Twitter post failed.")
        return 1
    lock_path.write_text(
        json.dumps({"date": chart_date, "posted": slugs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("[global-new] Priority Global card posted.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", help="Chart date YYYY-MM-DD")
    parser.add_argument("--post", action="store_true", help="Post to @tsmuseum13")
    parser.add_argument("--no-post", action="store_true", help="Generate only")
    parser.add_argument("--force", action="store_true", help="Ignore posted lock")
    parser.add_argument(
        "--force-song",
        action="append",
        default=[],
        help="Test mode: treat this title or track_id as NEW for this run.",
    )
    args = parser.parse_args()
    force_songs = set(args.force_song or [])
    if args.post and not args.no_post:
        return post_card(args.date, force=args.force, no_post=False, force_songs=force_songs)
    image_path = generate_card(args.date, force_songs=force_songs)
    return 0 if image_path is not None or not _load_priority_rows(args.date, force_songs=force_songs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
