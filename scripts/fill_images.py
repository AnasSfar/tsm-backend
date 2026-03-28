#!/usr/bin/env python3
"""
fill_images.py
--------------
Adds `image_url` to every track in the discography edition JSONs.

Priority order:
  1. Already present in the JSON (skip if already has image_url)
  2. songs.json  → track_id match
  3. covers.json → album name match  (album cover as fallback)
  4. Spotify oEmbed API (no auth required, rate-limited, last resort)

Also regenerates `spotify-charts/track_covers.json` used by ts_tracker.html,
mapping chart song names → correct album cover URL.

Usage:
  python scripts/fill_images.py              # normal run
  python scripts/fill_images.py --force      # overwrite existing image_url
  python scripts/fill_images.py --oembed     # enable oEmbed fetches for missing tracks
"""
import json
import re
import ssl
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT        = Path(__file__).parent.parent
DISCO_DIR   = ROOT / "db" / "discography"
SONGS_JSON  = ROOT / "website" / "site" / "data" / "songs.json"
COVERS_JSON = DISCO_DIR / "covers.json"
HIST_JSON  = ROOT / "collectors" / "spotify" / "charts" / "global" / "tools" / "json" / "ts_history.json"
OUT_COVERS = ROOT / "website" / "spotify-charts" / "track_covers.json"

FORCE  = "--force"  in sys.argv
OEMBED = True  # always fetch via oEmbed for tracks missing image_url

# ── helpers ────────────────────────────────────────────────────────────────

def extract_track_id(url: str) -> str | None:
    """Extract Spotify track ID from open.spotify.com/track/... URL."""
    m = re.search(r"/track/([A-Za-z0-9]+)", url or "")
    return m.group(1) if m else None


def oembed_image(track_url: str) -> str | None:
    """Fetch thumbnail_url via Spotify oEmbed (no auth needed)."""
    api = f"https://open.spotify.com/oembed?url={track_url}"
    try:
        req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as r:
            data = json.loads(r.read())
            thumb = data.get("thumbnail_url", "")
            # oEmbed gives a smaller size; upgrade to full 640x640
            # ab67616d00004851 (300px) → ab67616d0000b273 (640px)
            return (thumb
                    .replace("ab67616d00004851", "ab67616d0000b273")
                    .replace("ab67616d00001e02", "ab67616d0000b273"))
    except Exception as e:
        print(f"    [oEmbed ERROR] {e}")
        return None

# ── load reference data ────────────────────────────────────────────────────

print("Loading songs.json …")
with open(SONGS_JSON, encoding="utf-8") as f:
    songs_data = json.load(f)

# track_id → image_url
tid_to_img: dict[str, str] = {}
for s in songs_data.get("songs", []):
    tid = s.get("track_id")
    img = s.get("image_url")
    if tid and img:
        tid_to_img[tid] = img

print(f"  {len(tid_to_img)} tracks with images in songs.json")

print("Loading covers.json …")
with open(COVERS_JSON, encoding="utf-8") as f:
    covers_data: dict = json.load(f)

# album_name (lower) → cover_url
album_to_cover: dict[str, str] = {}
for key, val in covers_data.items():
    title = (val.get("title") or key).strip()
    url   = val.get("cover_url", "")
    if url:
        album_to_cover[title.lower()] = url
        album_to_cover[key.lower()]   = url  # also index by raw key

print(f"  {len(covers_data)} album covers loaded")

# Extra aliases for partial/variant album names
ttpd_cover = album_to_cover.get("the tortured poets department the anthology") or \
             album_to_cover.get("the_tortured_poets_department")
if ttpd_cover:
    album_to_cover["the tortured poets department"] = ttpd_cover

# ── process discography JSONs ──────────────────────────────────────────────

_SKIP_FILES   = {"covers.json", "artist.json"}
edition_files = [p for p in DISCO_DIR.rglob("*.json") if p.name not in _SKIP_FILES]
edition_files.sort()

total_tracks  = 0
already_had   = 0
from_songs    = 0
from_album    = 0
from_oembed   = 0
still_missing = 0

print(f"\nProcessing {len(edition_files)} edition files …\n")

for path in edition_files:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # Support both list-of-sections format and single dict-with-tracks format
    sections = data if isinstance(data, list) else [data]
    tracks = [t for section in sections for t in section.get("tracks", [])]
    changed = False

    for track in tracks:
        total_tracks += 1

        if track.get("image_url") and not FORCE:
            already_had += 1
            continue

        track_url = track.get("url", "")
        tid = extract_track_id(track_url)
        album_name = (track.get("album") or data.get("album") or "").strip()
        img = None

        # 1 — songs.json lookup by track_id
        if tid and tid in tid_to_img:
            img = tid_to_img[tid]
            from_songs += 1

        # 2 — covers.json lookup by album name
        if not img:
            key = album_name.lower()
            img = album_to_cover.get(key)
            if img:
                from_album += 1

        # 3 — oEmbed (optional, slow)
        if not img and OEMBED and track_url:
            print(f"  [oEmbed] {track.get('title')} …")
            img = oembed_image(track_url)
            if img:
                from_oembed += 1
            time.sleep(0.3)  # be polite

        if img:
            track["image_url"] = img
            changed = True
        else:
            still_missing += 1
            print(f"  [MISSING] {path.parent.name}/{path.name}: {track.get('title')}")

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  [OK] Updated {path.parent.name}/{path.name}  ({len(tracks)} tracks)")

# ── generate track_covers.json for ts_tracker ─────────────────────────────

print("\nGenerating track_covers.json for ts_tracker.html …")

if HIST_JSON.exists():
    with open(HIST_JSON, encoding="utf-8") as f:
        hist = json.load(f)
    chart_names = list(hist.keys())
    print(f"  {len(chart_names)} songs in ts_history.json")
else:
    print("  ts_history.json not found, skipping track_covers.json")
    chart_names = []

if chart_names:
    def norm_apos(s: str) -> str:
        """Normalize all apostrophe variants to ASCII straight quote."""
        return s.replace('\u2019', "'").replace('\u2018', "'").replace('\u02bc', "'")

    def normalise(name: str) -> str:
        """Lowercase + normalize apostrophes + strip feat/live/motion suffixes."""
        n = norm_apos(name).lower().strip()
        n = re.sub(r"\s*[\(\[]feat\..*?[\)\]]", "", n)
        n = re.sub(r"\s*-\s*live.*$", "", n)
        n = re.sub(r"\s*-\s*from the.*$", "", n)
        n = re.sub(r"\s*\(from the.*?\)", "", n)
        n = re.sub(r"\s+", " ", n).strip()
        return n

    # Build title → image_url from db/discography/songs.json + albums.json
    title_to_img: dict[str, str] = {}
    for disco_file in [DISCO_DIR / "albums.json", DISCO_DIR / "songs.json"]:
        if not disco_file.exists():
            continue
        sections = json.loads(disco_file.read_text(encoding="utf-8"))
        for section in (sections if isinstance(sections, list) else [sections]):
            for t in section.get("tracks", []):
                img = t.get("image_url", "")
                if not img:
                    continue
                for raw in [
                    (t.get("title") or "").lower(),
                    (t.get("base_title") or "").lower(),
                    (t.get("title_clean") or "").lower(),
                ]:
                    if raw:
                        title_to_img[raw] = img
                        normed = norm_apos(raw)
                        if normed != raw:
                            title_to_img[normed] = img

    track_covers: dict[str, str] = {}
    missing_names = []

    for name in chart_names:
        img = (title_to_img.get(name.lower())
               or title_to_img.get(norm_apos(name).lower())
               or title_to_img.get(normalise(name)))
        if img:
            track_covers[name] = img
        else:
            missing_names.append(name)

    # Fallback: read edition JSONs we just updated for any remaining
    if missing_names:
        # Build a map from normalised title → image_url from all edition files
        edition_map: dict[str, str] = {}
        for path in edition_files:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for t in data.get("tracks", []):
                img = t.get("image_url", "")
                if img:
                    for k in [t.get("title", ""), t.get("base_title", ""), t.get("title_clean", "")]:
                        if k:
                            edition_map[normalise(k)] = img

        still_missing_names = []
        for name in missing_names:
            img = edition_map.get(normalise(name))
            if img:
                track_covers[name] = img
            else:
                still_missing_names.append(name)
                print(f"  [CHART MISSING] {name}")
        missing_names = still_missing_names

    print(f"  Mapped {len(track_covers)}/{len(chart_names)} chart songs")
    if missing_names:
        print(f"  Still missing {len(missing_names)} (collabs / guest appearances)")

    OUT_COVERS.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_COVERS, "w", encoding="utf-8") as f:
        json.dump(track_covers, f, ensure_ascii=False, indent=2)
    print(f"  [OK] Written {OUT_COVERS}")

# ── summary ────────────────────────────────────────────────────────────────

print(f"""
Summary
-------
Total tracks processed : {total_tracks}
Already had image_url  : {already_had}
Filled from songs.json : {from_songs}
Filled from covers.json: {from_album}
Filled via oEmbed      : {from_oembed}
Still missing          : {still_missing}
""")
